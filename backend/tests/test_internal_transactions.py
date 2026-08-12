from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import (
    Account,
    AccountState,
    Campaign,
    CampaignState,
    Contact,
    JobState,
    MessageJob,
    MessageVariant,
    Role,
    User,
)
from app.routes.internal import claim_job


def test_claim_without_active_campaign_releases_account_lock(db):
    account = Account(
        external_id="chip_01",
        display_name="Chip 01",
        enabled=True,
        state=AccountState.ready,
        lease_owner="worker-1",
        lease_until=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db.add(account)
    db.commit()

    assert claim_job(account.id, "worker-1", db) is None
    assert db.in_transaction() is False


def test_claim_for_non_ready_account_releases_account_lock(db):
    account = Account(
        external_id="chip_02",
        display_name="Chip 02",
        enabled=True,
        state=AccountState.connecting,
        lease_owner="worker-1",
        lease_until=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db.add(account)
    db.commit()

    assert claim_job(account.id, "worker-1", db) is None
    assert db.in_transaction() is False


def test_claim_never_releases_job_without_scheduled_time(db):
    now = datetime.now(timezone.utc)
    user = User(email="schedule@example.com", password_hash="x", role=Role.admin)
    account = Account(
        external_id="chip_03",
        display_name="Chip 03",
        enabled=True,
        state=AccountState.ready,
        lease_owner="worker-1",
        lease_until=now + timedelta(minutes=1),
    )
    db.add_all([user, account])
    db.flush()
    campaign = Campaign(
        name="Cadência protegida",
        state=CampaignState.active,
        per_chip_daily_cap_snapshot=35,
        created_by_id=user.id,
    )
    db.add(campaign)
    db.flush()
    contact = Contact(campaign_id=campaign.id, phone="+5531999999999")
    variant = MessageVariant(campaign_id=campaign.id, body="Oi")
    db.add_all([contact, variant])
    db.flush()
    job = MessageJob(
        idempotency_key="missing-schedule",
        campaign_id=campaign.id,
        contact_id=contact.id,
        variant_id=variant.id,
        account_id=account.id,
        state=JobState.pending,
        scheduled_at=None,
    )
    db.add(job)
    db.commit()

    assert claim_job(account.id, "worker-1", db) is None
    assert job.state == JobState.pending
