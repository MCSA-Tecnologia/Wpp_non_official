from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user, require_admin
from ..events import broker
from ..models import Account, AccountAuthRecord, AccountState, AuditLog, User
from ..schemas import AccountBulkCreate, AccountCreate, AccountOut


router = APIRouter(prefix="/accounts", tags=["accounts"])


def activation_in_progress(db: Session, excluding: uuid.UUID) -> Account | None:
    return db.scalar(
        select(Account).where(
            Account.id != excluding,
            Account.enabled.is_(True),
            Account.state.in_([AccountState.connecting, AccountState.qr_required]),
        )
    )


@router.get("", response_model=list[AccountOut])
def list_accounts(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(Account).where(Account.in_fleet.is_(True)).order_by(Account.display_name)
        )
    )


@router.post("", response_model=AccountOut, status_code=201)
def create_account(
    payload: AccountCreate,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.scalar(select(Account).where(Account.external_id == payload.external_id)):
        raise HTTPException(status_code=409, detail="Identificador já cadastrado")
    account = Account(
        external_id=payload.external_id,
        display_name=payload.display_name,
        enabled=False,
        state=AccountState.disabled,
    )
    db.add(account)
    db.flush()
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="account.created",
            entity_type="account",
            entity_id=str(account.id),
        )
    )
    db.commit()
    db.refresh(account)
    return account


@router.post("/bulk", response_model=list[AccountOut])
async def bulk_create_accounts(
    payload: AccountBulkCreate,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    pattern = re.compile(rf"^{re.escape(payload.prefix)}_(\d+)$")
    managed: dict[int, Account] = {}
    for account in db.scalars(select(Account)):
        match = pattern.fullmatch(account.external_id)
        if match:
            managed[int(match.group(1))] = account

    created: list[Account] = []
    for number in range(1, payload.count + 1):
        external_id = f"{payload.prefix}_{number:02d}"
        account = managed.get(number)
        if not account:
            account = Account(
                external_id=external_id,
                display_name=f"Chip {number:02d}",
                enabled=False,
                state=AccountState.disabled,
            )
            db.add(account)
            created.append(account)
        elif not account.in_fleet:
            account.in_fleet = True
            account.enabled = False
            account.state = AccountState.disabled

    removed = [
        account
        for number, account in managed.items()
        if number > payload.count and account.in_fleet
    ]
    for account in removed:
        db.execute(delete(AccountAuthRecord).where(AccountAuthRecord.account_id == account.id))
        account.in_fleet = False
        account.enabled = False
        account.state = AccountState.disabled
        account.phone = None
        account.qr_code = None
        account.last_heartbeat_at = None
        account.last_error = None
        account.lease_until = None
        account.lease_owner = None
        account.node_id = None
        account.sent_today = 0
        account.sent_today_date = None
        account.reconnect_count = 0
        account.session_revision += 1

    db.add(
        AuditLog(
            actor_id=actor.id,
            action="account.fleet_synced",
            entity_type="account",
            details={
                "target_count": payload.count,
                "created_count": len(created),
                "removed_count": len(removed),
                "prefix": payload.prefix,
            },
        )
    )
    db.commit()
    await broker.publish(
        "dashboard",
        {"type": "accounts.changed", "count": payload.count},
    )
    await broker.publish(
        "workers",
        {"type": "accounts.changed", "count": payload.count},
    )
    return list(
        db.scalars(
            select(Account).where(Account.in_fleet.is_(True)).order_by(Account.display_name)
        )
    )


@router.post("/{account_id}/connect", response_model=AccountOut)
async def connect_account(
    account_id: uuid.UUID,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Chip não encontrado")
    if account.state == AccountState.ready:
        raise HTTPException(status_code=409, detail="Este chip já está pronto.")
    activating = activation_in_progress(db, account.id)
    if activating:
        raise HTTPException(
            status_code=409,
            detail=f"Conclua ou cancele a ativação de {activating.display_name} primeiro.",
        )
    account.enabled = True
    account.state = AccountState.connecting
    account.last_error = None
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="account.connect",
            entity_type="account",
            entity_id=str(account.id),
        )
    )
    db.commit()
    db.refresh(account)
    await broker.publish("dashboard", {"type": "account.connect", "account_id": str(account.id)})
    await broker.publish("workers", {"type": "accounts.changed", "account_id": str(account.id)})
    return account


@router.post("/{account_id}/disconnect", response_model=AccountOut)
async def disconnect_account(
    account_id: uuid.UUID,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Chip não encontrado")
    for record in db.scalars(
        select(AccountAuthRecord).where(AccountAuthRecord.account_id == account.id)
    ):
        db.delete(record)
    account.enabled = False
    account.state = AccountState.disabled
    account.phone = None
    account.qr_code = None
    account.last_heartbeat_at = None
    account.last_error = None
    account.lease_until = None
    account.lease_owner = None
    account.node_id = None
    account.session_revision += 1
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="account.disconnect",
            entity_type="account",
            entity_id=str(account.id),
        )
    )
    db.commit()
    db.refresh(account)
    await broker.publish("dashboard", {"type": "account.disconnect", "account_id": str(account.id)})
    await broker.publish("workers", {"type": "accounts.changed", "account_id": str(account.id)})
    return account


@router.delete("/{account_id}/session", status_code=204)
async def reset_session(
    account_id: uuid.UUID,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Chip não encontrado")
    activating = activation_in_progress(db, account.id)
    if activating:
        raise HTTPException(
            status_code=409,
            detail=f"Conclua ou cancele a ativação de {activating.display_name} primeiro.",
        )
    for record in db.scalars(
        select(AccountAuthRecord).where(AccountAuthRecord.account_id == account.id)
    ):
        db.delete(record)
    account.enabled = True
    account.state = AccountState.connecting
    account.qr_code = None
    account.phone = None
    account.last_error = None
    account.session_revision += 1
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="account.session_reset",
            entity_type="account",
            entity_id=str(account.id),
        )
    )
    db.commit()
    await broker.publish("workers", {"type": "accounts.changed", "account_id": str(account.id)})
    await broker.publish(
        "dashboard", {"type": "account.session_reset", "account_id": str(account.id)}
    )
