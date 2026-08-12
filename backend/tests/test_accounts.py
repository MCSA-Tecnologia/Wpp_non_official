from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Account, AccountAuthRecord, AccountState, Role, User
from app.routes import accounts as accounts_route


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
