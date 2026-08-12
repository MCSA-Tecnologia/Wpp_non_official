from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models import (
    Account,
    AccountState,
    Campaign,
    Contact,
    JobState,
    MessageJob,
    MessageVariant,
    Role,
    User,
)
from app.services.maintenance import recover_stale_state


def test_stale_worker_requeues_safe_job_and_reviews_sending_job(db):
    user = User(email="admin@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.flush()
    account = Account(
        external_id="chip_01",
        display_name="Chip 01",
        state=AccountState.ready,
        enabled=True,
        lease_owner="dead-worker",
        lease_until=datetime.now(timezone.utc) - timedelta(seconds=5),
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=2),
    )
    db.add(account)
    db.flush()
    campaign = Campaign(name="Teste", per_chip_daily_cap_snapshot=80, created_by_id=user.id)
    db.add(campaign)
    db.flush()
    contact_a = Contact(campaign_id=campaign.id, phone="+5531999999991")
    contact_b = Contact(campaign_id=campaign.id, phone="+5531999999992")
    db.add_all([contact_a, contact_b])
    db.flush()
    variant = MessageVariant(campaign_id=campaign.id, body="Oi")
    db.add(variant)
    db.flush()
    safe = MessageJob(
        idempotency_key="safe",
        campaign_id=campaign.id,
        contact_id=contact_a.id,
        variant_id=variant.id,
        account_id=account.id,
        state=JobState.leased,
        lease_token=str(uuid.uuid4()),
    )
    uncertain = MessageJob(
        idempotency_key="uncertain",
        campaign_id=campaign.id,
        contact_id=contact_b.id,
        variant_id=variant.id,
        account_id=account.id,
        state=JobState.sending,
        lease_token=str(uuid.uuid4()),
    )
    db.add_all([safe, uncertain])
    db.commit()

    result = recover_stale_state(db)
    assert result["requeued"] == 1
    assert result["review"] == 1
    assert safe.state == JobState.pending and safe.account_id is None
    assert uncertain.state == JobState.review_required


def test_expired_sending_job_is_reviewed_even_when_account_is_healthy(db):
    now = datetime.now(timezone.utc)
    user = User(email="healthy@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.flush()
    account = Account(
        external_id="chip_02",
        display_name="Chip 02",
        state=AccountState.ready,
        enabled=True,
        lease_owner="live-worker",
        lease_until=now + timedelta(minutes=1),
        last_heartbeat_at=now,
    )
    db.add(account)
    db.flush()
    campaign = Campaign(name="Travada", per_chip_daily_cap_snapshot=35, created_by_id=user.id)
    db.add(campaign)
    db.flush()
    contact = Contact(campaign_id=campaign.id, phone="+5531999999993")
    variant = MessageVariant(campaign_id=campaign.id, body="Oi")
    db.add_all([contact, variant])
    db.flush()
    job = MessageJob(
        idempotency_key="expired-sending-live-account",
        campaign_id=campaign.id,
        contact_id=contact.id,
        variant_id=variant.id,
        account_id=account.id,
        state=JobState.sending,
        lease_token=str(uuid.uuid4()),
        lease_until=now - timedelta(seconds=1),
        started_at=now - timedelta(minutes=2),
    )
    db.add(job)
    db.commit()

    result = recover_stale_state(db)

    assert result["review"] == 1
    assert job.state == JobState.review_required
    assert "retry automático bloqueado" in job.last_error
    assert account.state == AccountState.ready
