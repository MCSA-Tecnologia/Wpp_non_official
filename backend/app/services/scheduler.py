from __future__ import annotations

import heapq
import math
import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Account, AccountAuthRecord, AccountState, JobState, MessageJob


@dataclass
class ScheduleEntry:
    account_id: uuid.UUID
    scheduled_at: datetime


@dataclass
class SchedulePlan:
    entries: list[ScheduleEntry]
    effective_interval_minutes: float
    contacts_per_account: dict[str, int]
    start_at: datetime | None
    finish_at: datetime | None
    warnings: list[str]


def next_business_time(
    value: datetime, timezone_name: str, start_hour: int, end_hour: int
) -> datetime:
    zone = ZoneInfo(timezone_name)
    local = value.astimezone(zone)
    while True:
        if local.weekday() >= 5:
            days = 7 - local.weekday()
            local = datetime.combine(local.date() + timedelta(days=days), time(start_hour), zone)
            continue
        start = datetime.combine(local.date(), time(start_hour), zone)
        end = datetime.combine(local.date(), time(end_hour), zone)
        if local < start:
            return start.astimezone(timezone.utc)
        if local >= end:
            local = datetime.combine(local.date() + timedelta(days=1), time(start_hour), zone)
            continue
        return local.astimezone(timezone.utc)


def next_business_day(
    value: datetime, timezone_name: str, start_hour: int
) -> datetime:
    zone = ZoneInfo(timezone_name)
    local = value.astimezone(zone)
    candidate = datetime.combine(local.date() + timedelta(days=1), time(start_hour), zone)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def automatic_interval(
    contact_count: int,
    account_count: int,
    start_at: datetime,
    timezone_name: str,
    end_hour: int,
    safe_min: float,
    maximum: float,
) -> float:
    if contact_count <= account_count:
        return safe_min
    zone = ZoneInfo(timezone_name)
    local = start_at.astimezone(zone)
    end = datetime.combine(local.date(), time(end_hour), zone)
    remaining = max(safe_min, (end - local).total_seconds() / 60)
    per_account_intervals = max(1, math.ceil(contact_count / account_count) - 1)
    return round(min(maximum, max(safe_min, remaining / per_account_intervals)), 2)


def build_schedule(
    *,
    contact_count: int,
    accounts: list[Account],
    requested_interval_minutes: float,
    per_chip_daily_cap: int,
    timezone_name: str,
    start_hour: int,
    end_hour: int,
    now: datetime | None = None,
    jitter: bool = False,
    random_seed: str = "forecast",
) -> SchedulePlan:
    settings = get_settings()
    if contact_count <= 0:
        return SchedulePlan([], requested_interval_minutes, {}, None, None, [])
    if not accounts:
        return SchedulePlan([], 0, {}, None, None, ["Nenhum chip saudável disponível."])
    if per_chip_daily_cap <= 0:
        return SchedulePlan(
            [], 0, {}, None, None, ["Configure o teto diário por chip antes de agendar."]
        )

    current = now or datetime.now(timezone.utc)
    start = next_business_time(current, timezone_name, start_hour, end_hour)
    effective = requested_interval_minutes or automatic_interval(
        contact_count,
        len(accounts),
        start,
        timezone_name,
        end_hour,
        settings.safe_min_interval_minutes,
        settings.max_interval_minutes,
    )
    effective = min(
        settings.max_interval_minutes, max(settings.safe_min_interval_minutes, effective)
    )
    rng = random.Random(random_seed)

    heap: list[tuple[datetime, int, str, uuid.UUID, date]] = []
    zone = ZoneInfo(timezone_name)
    counts: dict[str, int] = {str(account.id): 0 for account in accounts}
    for account in accounts:
        local_day = start.astimezone(zone).date()
        used_today = account.sent_today if account.sent_today_date == local_day else 0
        heapq.heappush(heap, (start, used_today, str(account.id), account.id, local_day))

    entries: list[ScheduleEntry] = []
    for _ in range(contact_count):
        available, used_today, _, account_id, used_date = heapq.heappop(heap)
        available = next_business_time(available, timezone_name, start_hour, end_hour)
        available_date = available.astimezone(zone).date()
        if available_date != used_date:
            used_today = 0
            used_date = available_date
        if used_today >= per_chip_daily_cap:
            available = next_business_day(available, timezone_name, start_hour)
            used_today = 0
            used_date = available.astimezone(zone).date()
            available_date = used_date

        entries.append(ScheduleEntry(account_id=account_id, scheduled_at=available))
        counts[str(account_id)] += 1
        used_today += 1
        factor = rng.uniform(0.7, 1.3) if jitter else 1.0
        next_at = available + timedelta(minutes=effective * factor)
        next_at = next_business_time(next_at, timezone_name, start_hour, end_hour)
        heapq.heappush(heap, (next_at, used_today, str(account_id), account_id, used_date))

    warnings: list[str] = []
    if (
        entries[-1].scheduled_at.astimezone(zone).date()
        > entries[0].scheduled_at.astimezone(zone).date()
    ):
        warnings.append("A campanha ultrapassa a capacidade do dia e continuará na próxima janela.")
    names = {str(account.id): account.display_name for account in accounts}
    named_counts = {names[key]: value for key, value in counts.items()}
    return SchedulePlan(
        entries=entries,
        effective_interval_minutes=effective,
        contacts_per_account=named_counts,
        start_at=entries[0].scheduled_at,
        finish_at=entries[-1].scheduled_at,
        warnings=warnings,
    )


def healthy_accounts(db: Session) -> list[Account]:
    heartbeat_cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=get_settings().account_degraded_seconds
    )
    return list(
        db.scalars(
            select(Account)
            .where(
                Account.enabled.is_(True),
                Account.state == AccountState.ready,
                Account.phone.is_not(None),
                Account.last_heartbeat_at >= heartbeat_cutoff,
                exists().where(
                    AccountAuthRecord.account_id == Account.id,
                    AccountAuthRecord.category == "creds",
                    AccountAuthRecord.key_id == "default",
                ),
            )
            .order_by(Account.sent_today, Account.display_name)
        )
    )


def assign_campaign_jobs(
    db: Session, campaign_id: uuid.UUID, plan: SchedulePlan
) -> None:
    jobs = list(
        db.scalars(
            select(MessageJob)
            .where(
                MessageJob.campaign_id == campaign_id,
                MessageJob.state.in_([JobState.pending, JobState.scheduled]),
                MessageJob.lease_token.is_(None),
            )
            .order_by(MessageJob.id)
        )
    )
    if len(jobs) != len(plan.entries):
        raise ValueError("A quantidade de jobs não corresponde ao plano calculado.")
    for job, entry in zip(jobs, plan.entries, strict=True):
        job.account_id = entry.account_id
        job.scheduled_at = entry.scheduled_at
        job.state = JobState.scheduled


def campaign_counts(db: Session, campaign_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(
        select(MessageJob.state, func.count(MessageJob.id))
        .where(MessageJob.campaign_id == campaign_id)
        .group_by(MessageJob.state)
    ).all()
    result = {
        state.value if hasattr(state, "value") else str(state): count
        for state, count in rows
    }
    result["total"] = sum(result.values())
    return result
