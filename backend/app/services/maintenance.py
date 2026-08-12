from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Account, AccountState, JobState, MessageJob


def recover_stale_state(db: Session) -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    local_day = now.astimezone(ZoneInfo(settings.timezone)).date()
    for account in db.scalars(
        select(Account).where(
            or_(Account.sent_today_date.is_(None), Account.sent_today_date != local_day)
        ).with_for_update(skip_locked=True)
    ):
        account.sent_today = 0
        account.sent_today_date = local_day
    degraded_before = now - timedelta(seconds=settings.account_degraded_seconds)
    stale_accounts = list(
        db.scalars(
            select(Account).where(
                Account.enabled.is_(True),
                Account.last_heartbeat_at.is_not(None),
                Account.last_heartbeat_at < degraded_before,
                Account.state.in_(
                    [AccountState.ready, AccountState.connecting, AccountState.backoff]
                ),
            ).with_for_update(skip_locked=True)
        )
    )
    for account in stale_accounts:
        account.state = AccountState.degraded

    expired_accounts = list(
        db.scalars(
            select(Account).where(
                Account.lease_until.is_not(None), Account.lease_until < now
            ).with_for_update(skip_locked=True)
        )
    )
    requeued = uncertain = 0
    for account in expired_accounts:
        account.state = AccountState.offline if account.enabled else AccountState.disabled
        account.lease_owner = None
        account.lease_until = None
        account.node_id = None
        safe_jobs = list(
            db.scalars(
                select(MessageJob).where(
                    MessageJob.account_id == account.id,
                    MessageJob.state.in_([JobState.scheduled, JobState.leased]),
                ).with_for_update(skip_locked=True)
            )
        )
        for job in safe_jobs:
            job.state = JobState.pending
            job.account_id = None
            job.lease_token = None
            job.lease_until = None
            requeued += 1
        sending_jobs = list(
            db.scalars(
                select(MessageJob).where(
                    MessageJob.account_id == account.id,
                    MessageJob.state == JobState.sending,
                ).with_for_update(skip_locked=True)
            )
        )
        for job in sending_jobs:
            job.state = JobState.review_required
            job.lease_until = None
            job.last_error = "Worker perdeu o lease após iniciar o envio; resultado incerto."
            uncertain += 1
    # The account may remain healthy while one individual job lease expires
    # after an API/result failure. Recover that job independently so it cannot
    # freeze the campaign. A sending job is never retried automatically.
    expired_jobs = list(
        db.scalars(
            select(MessageJob)
            .where(
                MessageJob.lease_until.is_not(None),
                MessageJob.lease_until < now,
                MessageJob.state.in_([JobState.leased, JobState.sending]),
            )
            .with_for_update(skip_locked=True)
        )
    )
    for job in expired_jobs:
        if job.state == JobState.sending:
            job.state = JobState.review_required
            job.lease_until = None
            job.last_error = (
                "O lease do envio venceu após o WhatsApp ser acionado; "
                "resultado incerto e retry automático bloqueado."
            )
            uncertain += 1
        else:
            job.state = JobState.pending
            job.account_id = None
            job.lease_token = None
            job.lease_until = None
            requeued += 1
    db.commit()
    return {
        "degraded": len(stale_accounts),
        "expired": len(expired_accounts),
        "requeued": requeued,
        "review": uncertain,
    }
