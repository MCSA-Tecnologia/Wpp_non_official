from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Campaign, CampaignState, DeliveryEvent, JobState, MessageJob


IN_PROGRESS_JOB_STATES = (
    JobState.pending,
    JobState.scheduled,
    JobState.leased,
    JobState.sending,
)


def _has_in_progress_jobs(db: Session, campaign_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(MessageJob.id)
            .where(
                MessageJob.campaign_id == campaign_id,
                MessageJob.state.in_(IN_PROGRESS_JOB_STATES),
            )
            .limit(1)
        )
        is not None
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _review_unconfirmed_jobs(
    db: Session,
    campaign: Campaign,
    now: datetime,
    grace_seconds: int,
) -> tuple[bool, bool]:
    """Resolve sent jobs truthfully after the receipt grace period.

    Returns (has_unresolved_receipts, changed). SERVER_ACK (2) is a successful
    send even without a delivery receipt. A job with no server ACK or error is
    still uncertain and goes to human review.
    """

    jobs = list(
        db.scalars(
            select(MessageJob).where(
                MessageJob.campaign_id == campaign.id,
                MessageJob.state == JobState.sent,
            )
        )
    )
    if not jobs:
        return False, False

    last_sent_at = max(
        _as_utc(
            job.sent_at
            or job.started_at
            or campaign.finished_at
            or campaign.started_at
            or campaign.created_at
        )
        for job in jobs
    )
    if now < last_sent_at + timedelta(seconds=grace_seconds):
        return True, False

    changed = False
    for job in jobs:
        highest_ack = db.scalar(
            select(func.max(DeliveryEvent.ack_level)).where(DeliveryEvent.job_id == job.id)
        )
        if highest_ack is not None and highest_ack >= 2:
            job.last_error = None
            continue
        job.state = JobState.review_required
        job.last_error = (
            "O WhatsApp não confirmou a aceitação nem retornou erro em até 2 minutos."
        )
        changed = True
    return False, changed


def _reconcile_campaign(
    db: Session,
    campaign: Campaign,
    now: datetime,
    grace_seconds: int,
) -> bool:
    """Move a campaign through sending, receipt wait and truthful completion."""

    if _has_in_progress_jobs(db, campaign.id):
        return False

    awaiting_receipts, changed = _review_unconfirmed_jobs(
        db, campaign, now, grace_seconds
    )
    if awaiting_receipts:
        if campaign.state != CampaignState.awaiting_results:
            campaign.state = CampaignState.awaiting_results
            campaign.finished_at = None
            changed = True
        return changed

    if campaign.state != CampaignState.completed:
        campaign.state = CampaignState.completed
        campaign.finished_at = now
        changed = True
    return changed


def maybe_complete_campaign(
    db: Session,
    campaign_id: uuid.UUID,
    *,
    now: datetime | None = None,
    grace_seconds: int | None = None,
) -> bool:
    """Reconcile a campaign after a job result or WhatsApp receipt.

    Production sessions disable autoflush, so the current job must be flushed before
    querying its campaign. Locking the campaign serializes simultaneous results from
    different workers and prevents both transactions from observing the other as pending.
    """

    db.flush()
    campaign = db.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    )
    if not campaign or campaign.state not in (
        CampaignState.active,
        CampaignState.awaiting_results,
    ):
        return False
    current_time = _as_utc(now or datetime.now(timezone.utc))
    return _reconcile_campaign(
        db,
        campaign,
        current_time,
        grace_seconds if grace_seconds is not None else get_settings().result_grace_seconds,
    )


def reconcile_completed_campaigns(
    db: Session,
    *,
    now: datetime | None = None,
    grace_seconds: int | None = None,
) -> int:
    """Finalize receipt waits and repair campaigns completed too early by old code."""

    campaigns = list(
        db.scalars(
            select(Campaign)
            .where(
                or_(
                    Campaign.state.in_(
                        [CampaignState.active, CampaignState.awaiting_results]
                    ),
                    exists(
                        select(MessageJob.id).where(
                            MessageJob.campaign_id == Campaign.id,
                            MessageJob.state == JobState.sent,
                            ~exists(
                                select(DeliveryEvent.id).where(
                                    DeliveryEvent.job_id == MessageJob.id,
                                    DeliveryEvent.ack_level >= 2,
                                )
                            ),
                        )
                    ),
                )
            )
            .with_for_update(skip_locked=True)
        )
    )
    changed = 0
    current_time = _as_utc(now or datetime.now(timezone.utc))
    receipt_grace = (
        grace_seconds if grace_seconds is not None else get_settings().result_grace_seconds
    )
    for campaign in campaigns:
        if _reconcile_campaign(db, campaign, current_time, receipt_grace):
            changed += 1
    db.commit()
    return changed
