from __future__ import annotations

from datetime import datetime, timezone
import uuid
from collections import Counter
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import (
    Account,
    AccountAuthRecord,
    AccountState,
    ImportBatch,
    ImportRow,
    ImportState,
    MessageJob,
    Role,
    User,
)
from app.routes.campaigns import prepare_campaign_start
from app.schemas import CampaignCreate
from app.services.campaigns import create_campaign
from app.services.scheduler import build_schedule, healthy_accounts


def accounts(count: int) -> list[Account]:
    return [
        Account(
            id=uuid.uuid4(),
            external_id=f"chip_{number:02d}",
            display_name=f"Chip {number:02d}",
            state=AccountState.ready,
        )
        for number in range(1, count + 1)
    ]


def test_two_thousand_contacts_are_balanced_without_duplicates():
    fleet = accounts(30)
    plan = build_schedule(
        contact_count=2000,
        accounts=fleet,
        requested_interval_minutes=0,
        per_chip_daily_cap=2000,
        timezone_name="America/Sao_Paulo",
        start_hour=9,
        end_hour=18,
        now=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
    )
    assert len(plan.entries) == 2000
    assert len(plan.contacts_per_account) == 30
    assert max(plan.contacts_per_account.values()) - min(plan.contacts_per_account.values()) <= 1
    assert max(plan.contacts_per_account.values()) == 67
    assert plan.start_at is not None and plan.finish_at is not None
    assert len({(entry.account_id, entry.scheduled_at) for entry in plan.entries}) == 2000


def test_per_chip_daily_cap_moves_remainder_to_next_business_window():
    plan = build_schedule(
        contact_count=20,
        accounts=accounts(2),
        requested_interval_minutes=1,
        per_chip_daily_cap=5,
        timezone_name="America/Sao_Paulo",
        start_hour=9,
        end_hour=18,
        now=datetime(2026, 8, 14, 15, tzinfo=timezone.utc),  # Friday noon in São Paulo
    )
    assert plan.warnings
    assert plan.finish_at.astimezone().date() > plan.start_at.astimezone().date()
    per_account_day = Counter(
        (entry.account_id, entry.scheduled_at.astimezone(ZoneInfo("America/Sao_Paulo")).date())
        for entry in plan.entries
    )
    assert max(per_account_day.values()) <= 5


def test_no_healthy_account_returns_actionable_warning():
    plan = build_schedule(
        contact_count=100,
        accounts=[],
        requested_interval_minutes=2,
        per_chip_daily_cap=80,
        timezone_name="America/Sao_Paulo",
        start_hour=9,
        end_hour=18,
    )
    assert not plan.entries
    assert "Nenhum chip" in plan.warnings[0]


def test_only_authenticated_recent_accounts_are_healthy(db):
    now = datetime.now(timezone.utc)
    authenticated = Account(
        external_id="chip_01",
        display_name="Chip 01",
        state=AccountState.ready,
        enabled=True,
        phone="+5531999999999",
        last_heartbeat_at=now,
    )
    missing_auth = Account(
        external_id="chip_02",
        display_name="Chip 02",
        state=AccountState.ready,
        enabled=True,
        phone="+5531888888888",
        last_heartbeat_at=now,
    )
    db.add_all([authenticated, missing_auth])
    db.flush()
    db.add(
        AccountAuthRecord(
            account_id=authenticated.id,
            category="creds",
            key_id="default",
            ciphertext=b"encrypted",
        )
    )
    db.commit()

    assert [account.external_id for account in healthy_accounts(db)] == ["chip_01"]


def test_every_imported_job_is_flushed_and_receives_per_chip_schedule(db, configure_message_card):
    now = datetime.now(timezone.utc)
    user = User(email="cadence@example.com", password_hash="x", role=Role.admin)
    db.add(user)
    db.flush()
    account = Account(
        external_id="chip_cadence",
        display_name="Chip Cadência",
        state=AccountState.ready,
        enabled=True,
        phone="+5531999999999",
        last_heartbeat_at=now,
    )
    db.add(account)
    db.flush()
    db.add(
        AccountAuthRecord(
            account_id=account.id,
            category="creds",
            key_id="default",
            ciphertext=b"encrypted",
        )
    )
    batch = ImportBatch(
        filename="dois.csv",
        state=ImportState.ready,
        total_rows=2,
        valid_rows=2,
        invalid_rows=0,
        duplicate_rows=0,
        created_by_id=user.id,
    )
    db.add(batch)
    db.flush()
    db.add_all(
        [
            ImportRow(
                batch_id=batch.id,
                row_number=index,
                raw_data={"Telefone": phone, "Nome": f"Pessoa {index}"},
                normalized_phone=phone,
                valid=True,
                duplicate=False,
            )
            for index, phone in enumerate(
                ["+5531988888881", "+5531988888882"], start=1
            )
        ]
    )
    db.commit()
    card = configure_message_card(user)

    campaign = create_campaign(
        db,
        CampaignCreate(
            name="Cadência",
            import_id=batch.id,
            message="Olá NOME_DO_CLIENTE",
            card_revision=card["revision"],
            interval_mean_minutes=2,
            confirmed_real_send=True,
        ),
        user,
        35,
        commit=False,
    )
    prepare_campaign_start(db, campaign, user)
    db.commit()
    jobs = list(
        db.scalars(
            select(MessageJob)
            .where(MessageJob.campaign_id == campaign.id)
            .order_by(MessageJob.scheduled_at)
        )
    )

    assert len(jobs) == 2
    assert all(job.scheduled_at is not None for job in jobs)
    gap_seconds = (jobs[1].scheduled_at - jobs[0].scheduled_at).total_seconds()
    assert 84 <= gap_seconds <= 156  # two-minute mean with the documented 70%-130% jitter
