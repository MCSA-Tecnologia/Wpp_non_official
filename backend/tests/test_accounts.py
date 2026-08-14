from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Account, AccountAuthRecord, AccountState, AuditLog, Role, User
from app.routes import accounts as accounts_route
from app.schemas import AccountBulkCreate


async def noop_publish(channel: str, event: dict) -> None:
    return None


def admin(db) -> User:
    actor = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash="hash",
        role=Role.admin,
    )
    db.add(actor)
    db.commit()
    return actor


def sync_fleet(db, actor: User, count: int):
    return asyncio.run(
        accounts_route.bulk_create_accounts(AccountBulkCreate(count=count), actor, db)
    )


def test_fleet_expansion_preserves_existing_chips_and_only_initializes_new_ones(db, monkeypatch):
    monkeypatch.setattr(accounts_route.broker, "publish", noop_publish)
    actor = admin(db)
    sync_fleet(db, actor, 30)
    chip_01 = db.scalar(select(Account).where(Account.external_id == "chip_01"))
    assert chip_01 is not None
    original_id = chip_01.id
    chip_01.enabled = True
    chip_01.state = AccountState.ready
    chip_01.phone = "5511999999999"
    db.add(
        AccountAuthRecord(
            account_id=chip_01.id,
            category="creds",
            key_id="default",
            ciphertext=b"encrypted",
        )
    )
    db.commit()

    result = sync_fleet(db, actor, 32)

    assert len(result) == 32
    preserved = db.scalar(select(Account).where(Account.external_id == "chip_01"))
    assert preserved.id == original_id
    assert preserved.phone == "5511999999999"
    chip_31 = db.scalar(select(Account).where(Account.external_id == "chip_31"))
    assert chip_31 is not None
    assert chip_31.enabled is False
    assert chip_31.state == AccountState.disabled
    audit = db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "account.fleet_synced")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit.details["created_count"] == 2
    assert audit.details["removed_count"] == 0


def test_fleet_reduction_clears_out_of_range_chip_configuration(db, monkeypatch):
    monkeypatch.setattr(accounts_route.broker, "publish", noop_publish)
    actor = admin(db)
    sync_fleet(db, actor, 32)
    chip_31 = db.scalar(select(Account).where(Account.external_id == "chip_31"))
    chip_31.enabled = True
    chip_31.state = AccountState.ready
    chip_31.phone = "5511888888888"
    chip_31.node_id = "node-1"
    chip_31.lease_owner = "worker-1"
    db.add(
        AccountAuthRecord(
            account_id=chip_31.id,
            category="creds",
            key_id="default",
            ciphertext=b"encrypted",
        )
    )
    db.commit()

    reduced = sync_fleet(db, actor, 20)

    assert len(reduced) == 20
    db.refresh(chip_31)
    assert chip_31.in_fleet is False
    assert chip_31.enabled is False
    assert chip_31.state == AccountState.disabled
    assert chip_31.phone is None
    assert chip_31.node_id is None
    assert chip_31.lease_owner is None
    assert db.scalar(
        select(AccountAuthRecord).where(AccountAuthRecord.account_id == chip_31.id)
    ) is None

    expanded = sync_fleet(db, actor, 22)
    assert len(expanded) == 22
    chip_21 = db.scalar(select(Account).where(Account.external_id == "chip_21"))
    assert chip_21.in_fleet is True
    assert chip_21.enabled is False
    assert chip_21.phone is None
    assert db.scalar(
        select(AccountAuthRecord).where(AccountAuthRecord.account_id == chip_21.id)
    ) is None


def test_operator_disconnect_removes_saved_session_and_disables_chip(db, monkeypatch):
    actor = User(
        id=uuid.uuid4(),
        email="operator@example.com",
        password_hash="hash",
        role=Role.operator,
    )
    account = Account(
        external_id="chip_01",
        display_name="Chip 01",
        enabled=True,
        state=AccountState.ready,
        phone="5511999999999",
        node_id="node-1",
        lease_owner="worker-1",
        lease_until=datetime.now(timezone.utc) + timedelta(minutes=1),
        last_heartbeat_at=datetime.now(timezone.utc),
        qr_code="stale-qr",
        session_revision=3,
    )
    db.add_all([actor, account])
    db.flush()
    db.add(
        AccountAuthRecord(
            account_id=account.id,
            category="creds",
            key_id="default",
            ciphertext=b"encrypted",
        )
    )
    db.commit()

    published: list[tuple[str, dict]] = []

    async def publish(channel: str, event: dict) -> None:
        published.append((channel, event))

    monkeypatch.setattr(accounts_route.broker, "publish", publish)

    result = asyncio.run(accounts_route.disconnect_account(account.id, actor, db))

    assert result.state == AccountState.disabled
    assert result.enabled is False
    assert result.phone is None
    assert result.node_id is None
    assert result.lease_owner is None
    assert result.lease_until is None
    assert result.last_heartbeat_at is None
    assert result.qr_code is None
    assert result.session_revision == 4
    assert db.scalar(
        select(AccountAuthRecord).where(AccountAuthRecord.account_id == account.id)
    ) is None
    assert [channel for channel, _ in published] == ["dashboard", "workers"]
