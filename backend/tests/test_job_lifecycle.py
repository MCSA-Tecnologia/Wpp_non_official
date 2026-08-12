from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks

from app.models import (
    Account,
    AccountState,
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
from app.routes import internal
from app.schemas import AckEvent, JobResult


def build_leased_job(db):
    user = User(email="admin@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.flush()
    account = Account(
        external_id="chip_01",
        display_name="Chip 01",
        state=AccountState.ready,
        enabled=True,
        lease_owner="worker-1",
        lease_until=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db.add(account)
    db.flush()
    campaign = Campaign(
        name="Envio real",
        state=CampaignState.active,
        per_chip_daily_cap_snapshot=30,
        created_by_id=user.id,
    )
    db.add(campaign)
    db.flush()
    contact = Contact(campaign_id=campaign.id, phone="+5511999999999")
    variant = MessageVariant(campaign_id=campaign.id, body="Olá")
    db.add_all([contact, variant])
    db.flush()
    lease_token = str(uuid.uuid4())
    job = MessageJob(
        idempotency_key="real-job-1",
        campaign_id=campaign.id,
        contact_id=contact.id,
        variant_id=variant.id,
        account_id=account.id,
        state=JobState.sending,
        lease_token=lease_token,
    )
    db.add(job)
    db.commit()
    return account, campaign, job, lease_token


def test_sent_result_waits_for_receipt_and_real_delivery_requires_ack_3(db, monkeypatch):
    account, campaign, job, lease_token = build_leased_job(db)

    async def publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(internal.broker, "publish", publish)

    internal.job_result(
        account.id,
        job.id,
        "worker-1",
        JobResult(
            lease_token=lease_token,
            state="sent",
            provider_message_id="provider-real-1",
        ),
        BackgroundTasks(),
        db,
    )

    assert job.state == JobState.sent
    assert job.provider_message_id == "provider-real-1"
    assert job.sent_at is not None
    assert account.sent_today == 1
    assert campaign.state == CampaignState.awaiting_results
    assert campaign.finished_at is None

    internal.ack_event(
        AckEvent(provider_message_id="provider-real-1", ack_level=2),
        BackgroundTasks(),
        db,
    )
    assert job.state == JobState.sent
    assert job.delivered_at is None
    assert campaign.state == CampaignState.awaiting_results

    internal.ack_event(
        AckEvent(provider_message_id="provider-real-1", ack_level=3),
        BackgroundTasks(),
        db,
    )
    assert job.state == JobState.delivered
    assert job.delivered_at is not None
    assert campaign.state == CampaignState.completed
    assert campaign.finished_at is not None
    assert db.query(DeliveryEvent).filter_by(job_id=job.id).count() == 2


def test_explicit_whatsapp_error_marks_job_failed_and_completes_campaign(db, monkeypatch):
    account, campaign, job, lease_token = build_leased_job(db)

    async def publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(internal.broker, "publish", publish)
    internal.job_result(
        account.id,
        job.id,
        "worker-1",
        JobResult(
            lease_token=lease_token,
            state="sent",
            provider_message_id="provider-error-1",
        ),
        BackgroundTasks(),
        db,
    )

    internal.ack_event(
        AckEvent(
            provider_message_id="provider-error-1",
            ack_level=0,
            payload={"error": "recipient unavailable"},
        ),
        BackgroundTasks(),
        db,
    )

    assert job.state == JobState.failed
    assert "recipient unavailable" in job.last_error
    assert campaign.state == CampaignState.completed
    assert campaign.finished_at is not None
