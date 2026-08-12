from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import (
    Campaign,
    CampaignState,
    Contact,
    DeliveryEvent,
    JobState,
    MessageJob,
    MessageVariant,
    Role,
    User,
)
from app.routes.campaigns import serialize_campaign
from app.routes.operations import reviews
from app.services.campaign_state import maybe_complete_campaign, reconcile_completed_campaigns


def campaign_with_jobs(db, states: list[JobState]) -> tuple[Campaign, list[MessageJob]]:
    user = User(email=f"admin-{len(states)}@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.flush()
    campaign = Campaign(
        name="Campanha",
        state=CampaignState.active,
        per_chip_daily_cap_snapshot=30,
        created_by_id=user.id,
    )
    db.add(campaign)
    db.flush()
    variant = MessageVariant(campaign_id=campaign.id, body="Oi")
    db.add(variant)
    db.flush()
    jobs: list[MessageJob] = []
    for index, state in enumerate(states):
        contact = Contact(campaign_id=campaign.id, phone=f"+55119999999{index:02d}")
        db.add(contact)
        db.flush()
        jobs.append(
            MessageJob(
                idempotency_key=f"job-{campaign.id}-{index}",
                campaign_id=campaign.id,
                contact_id=contact.id,
                variant_id=variant.id,
                state=state,
                sent_at=(
                    datetime.now(timezone.utc) - timedelta(minutes=3)
                    if state == JobState.sent
                    else None
                ),
            )
        )
    db.add_all(jobs)
    db.commit()
    return campaign, jobs


def test_current_sent_job_is_flushed_before_receipt_wait_query(db):
    campaign, jobs = campaign_with_jobs(db, [JobState.sent, JobState.sending])
    now = datetime.now(timezone.utc)
    jobs[0].sent_at = now
    jobs[-1].state = JobState.sent
    jobs[-1].sent_at = now

    assert maybe_complete_campaign(db, campaign.id, now=now, grace_seconds=115) is True

    db.commit()
    assert campaign.state == CampaignState.awaiting_results
    assert campaign.finished_at is None


def test_reconciler_completes_old_active_campaign_with_terminal_jobs(db):
    campaign, jobs = campaign_with_jobs(db, [JobState.sent, JobState.delivered])

    assert reconcile_completed_campaigns(
        db, now=datetime.now(timezone.utc), grace_seconds=115
    ) == 1
    assert campaign.state == CampaignState.completed
    assert campaign.finished_at is not None
    assert jobs[0].state == JobState.review_required
    assert "não confirmou" in jobs[0].last_error


def test_server_ack_is_successful_send_even_without_delivery_receipt(db):
    campaign, jobs = campaign_with_jobs(db, [JobState.sent])
    db.add(
        DeliveryEvent(
            job_id=jobs[0].id,
            provider_message_id="server-accepted",
            ack_level=2,
            payload={},
        )
    )
    db.commit()

    assert reconcile_completed_campaigns(
        db, now=datetime.now(timezone.utc), grace_seconds=115
    ) == 1
    assert campaign.state == CampaignState.completed
    assert jobs[0].state == JobState.sent
    assert jobs[0].last_error is None


def test_reconciler_keeps_campaign_with_scheduled_job_active(db):
    campaign, _ = campaign_with_jobs(db, [JobState.sent, JobState.scheduled])

    assert reconcile_completed_campaigns(db) == 0
    assert campaign.state == CampaignState.active


def test_reconciler_keeps_recent_sent_job_waiting_for_real_receipt(db):
    campaign, jobs = campaign_with_jobs(db, [JobState.sent])
    now = datetime.now(timezone.utc)
    jobs[0].sent_at = now - timedelta(seconds=30)
    db.commit()

    assert reconcile_completed_campaigns(db, now=now, grace_seconds=115) == 1
    assert campaign.state == CampaignState.awaiting_results
    assert jobs[0].state == JobState.sent


def test_reconciler_completes_immediately_when_every_result_is_definitive(db):
    campaign, _ = campaign_with_jobs(db, [JobState.delivered, JobState.failed])

    assert reconcile_completed_campaigns(db) == 1
    assert campaign.state == CampaignState.completed


def test_campaign_api_exposes_delivered_failed_and_unconfirmed_counts(db):
    campaign, jobs = campaign_with_jobs(
        db,
        [JobState.delivered, JobState.failed, JobState.review_required],
    )
    sent_at = datetime.now(timezone.utc)
    for job in jobs:
        job.sent_at = sent_at
    db.commit()

    result = serialize_campaign(db, campaign)

    assert result.sent == 3
    assert result.delivered == 1
    assert result.failed == 1
    assert result.review_required == 1


def test_attention_api_includes_failures_and_unconfirmed_results(db):
    _, _ = campaign_with_jobs(db, [JobState.failed, JobState.review_required])

    result = reviews(None, db)

    assert {item.state for item in result} == {
        JobState.failed,
        JobState.review_required,
    }
