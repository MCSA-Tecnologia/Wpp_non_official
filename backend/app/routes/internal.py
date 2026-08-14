from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dependencies import require_worker
from ..events import broker
from ..models import (
    Account,
    AccountAuthRecord,
    AccountState,
    Campaign,
    CampaignState,
    Contact,
    DeliveryEvent,
    JobState,
    MessageAttempt,
    MessageCardAsset,
    MessageJob,
    MessageVariant,
    RORegistration,
)
from ..schemas import (
    AckEvent,
    AuthBulkPayload,
    ClaimedJob,
    JobResult,
    WorkerClaimRequest,
    WorkerHeartbeat,
)
from ..security import decrypt_json, encrypt_json
from ..services.campaign_state import maybe_complete_campaign
from ..services.campaigns import materialize_job_message

router = APIRouter(prefix="/internal", tags=["worker"], dependencies=[Depends(require_worker)])


def _ack_error_detail(payload: AckEvent) -> str:
    for key in ("error", "message", "reason", "statusText"):
        value = payload.payload.get(key)
        if value:
            return f"WhatsApp retornou erro no envio: {value}"
    return "WhatsApp retornou erro explícito após o envio."


@router.get("/events")
async def worker_events():
    async def stream():
        async for event in broker.subscribe("workers"):
            yield f"data: {event}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/assets/{asset_id}")
def get_card_asset(asset_id: uuid.UUID, db: Session = Depends(get_db)):
    asset = db.get(MessageCardAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Imagem da mensagem não encontrada")
    return Response(
        asset.content,
        media_type=asset.content_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable", "ETag": asset.sha256},
    )


def owned_account(db: Session, account_id: uuid.UUID, worker_id: str) -> Account:
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if not account or account.lease_owner != worker_id:
        raise HTTPException(status_code=409, detail="Worker não possui o lease deste chip")
    return account


@router.post("/workers/claim-accounts")
def claim_accounts(payload: WorkerClaimRequest, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=get_settings().account_lease_seconds)
    existing = list(
        db.scalars(
            select(Account)
            .where(Account.enabled.is_(True), Account.lease_owner == payload.worker_id)
            .order_by(Account.display_name)
            .with_for_update(skip_locked=True)
        )
    )
    available_slots = max(0, payload.capacity - len(existing))
    claimed = existing
    if available_slots:
        candidates = list(
            db.scalars(
                select(Account)
                .where(
                    Account.enabled.is_(True),
                    or_(Account.lease_owner.is_(None), Account.lease_until < now),
                )
                .order_by(Account.last_heartbeat_at.asc().nullsfirst(), Account.display_name)
                .limit(available_slots)
                .with_for_update(skip_locked=True)
            )
        )
        claimed.extend(candidates)
    for account in claimed:
        account.lease_owner = payload.worker_id
        account.node_id = payload.node_id
        account.lease_until = lease_until
        if account.state in (AccountState.offline, AccountState.degraded, AccountState.backoff):
            account.state = AccountState.connecting
    db.commit()
    return [
        {
            "id": str(account.id),
            "external_id": account.external_id,
            "display_name": account.display_name,
            "state": account.state,
            "lease_until": account.lease_until,
            "session_revision": account.session_revision,
        }
        for account in claimed
    ]


@router.post("/accounts/{account_id}/heartbeat")
def heartbeat(
    account_id: uuid.UUID,
    payload: WorkerHeartbeat,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    account = owned_account(db, account_id, payload.worker_id)
    now = datetime.now(timezone.utc)
    account.node_id = payload.node_id
    account.last_heartbeat_at = now
    account.lease_until = now + timedelta(seconds=get_settings().account_lease_seconds)
    account.phone = payload.phone or account.phone
    if payload.state == AccountState.ready:
        has_credentials = db.scalar(
            select(AccountAuthRecord.id).where(
                AccountAuthRecord.account_id == account.id,
                AccountAuthRecord.category == "creds",
                AccountAuthRecord.key_id == "default",
            )
        )
        if not has_credentials or not account.phone:
            raise HTTPException(
                status_code=409,
                detail="Chip sem sessão autenticada persistida ou número identificado.",
            )
    account.state = payload.state
    account.last_error = payload.error
    if payload.qr_code is not None:
        account.qr_code = payload.qr_code
    elif payload.state == AccountState.ready:
        account.qr_code = None
    db.commit()
    background_tasks.add_task(
        broker.publish,
        "dashboard",
        {
            "type": "account.status",
            "account_id": str(account.id),
            "state": account.state.value,
            "node_id": account.node_id,
            "error": account.last_error,
            "qr_code": account.qr_code,
            "at": now.isoformat(),
        },
    )
    return {"lease_until": account.lease_until}


@router.get("/accounts/{account_id}/auth")
def get_auth_records(
    account_id: uuid.UUID, worker_id: str, db: Session = Depends(get_db)
):
    owned_account(db, account_id, worker_id)
    records = db.scalars(
        select(AccountAuthRecord).where(AccountAuthRecord.account_id == account_id)
    )
    payload = [
        {"category": item.category, "key_id": item.key_id, "value": decrypt_json(item.ciphertext)}
        for item in records
    ]
    db.rollback()
    return payload


@router.put("/accounts/{account_id}/auth")
def save_auth_records(
    account_id: uuid.UUID,
    worker_id: str,
    payload: AuthBulkPayload,
    db: Session = Depends(get_db),
):
    owned_account(db, account_id, worker_id)
    for item in payload.records:
        record = db.scalar(
            select(AccountAuthRecord).where(
                AccountAuthRecord.account_id == account_id,
                AccountAuthRecord.category == item.category,
                AccountAuthRecord.key_id == item.key_id,
            )
        )
        if item.value is None:
            if record:
                db.delete(record)
            continue
        if not record:
            record = AccountAuthRecord(
                account_id=account_id, category=item.category, key_id=item.key_id, ciphertext=b""
            )
            db.add(record)
        record.ciphertext = encrypt_json(item.value)
    db.commit()
    return {"saved": len(payload.records)}


@router.delete("/accounts/{account_id}/auth", status_code=204)
def delete_auth_records(
    account_id: uuid.UUID, worker_id: str, db: Session = Depends(get_db)
):
    owned_account(db, account_id, worker_id)
    db.execute(delete(AccountAuthRecord).where(AccountAuthRecord.account_id == account_id))
    db.commit()


@router.post("/accounts/{account_id}/jobs/claim", response_model=ClaimedJob | None)
def claim_job(
    account_id: uuid.UUID, worker_id: str, db: Session = Depends(get_db)
):
    account = owned_account(db, account_id, worker_id)
    if account.state != AccountState.ready:
        db.rollback()
        return None
    now = datetime.now(timezone.utc)
    campaign = db.scalar(select(Campaign).where(Campaign.state == CampaignState.active))
    if not campaign:
        db.rollback()
        return None
    local_day = now.astimezone(ZoneInfo(campaign.timezone)).date()
    if account.sent_today_date != local_day:
        account.sent_today = 0
        account.sent_today_date = local_day
    if account.sent_today >= campaign.per_chip_daily_cap_snapshot:
        db.commit()
        return None
    conditions = [
        MessageJob.campaign_id == campaign.id,
        MessageJob.state.in_([JobState.pending, JobState.scheduled]),
        or_(MessageJob.account_id == account.id, MessageJob.account_id.is_(None)),
        MessageJob.scheduled_at.is_not(None),
        MessageJob.scheduled_at <= now,
    ]
    job = db.scalar(
        select(MessageJob)
        .where(*conditions)
        .order_by(MessageJob.scheduled_at.asc().nullsfirst(), MessageJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if not job:
        db.commit()
        return None
    contact = db.get(Contact, job.contact_id)
    variant = db.get(MessageVariant, job.variant_id)
    if not contact or not variant:
        job.state = JobState.failed
        job.last_error = "Contato ou mensagem não encontrados"
        db.commit()
        return None
    if not variant.card_text or not variant.card_url or not variant.card_asset_id:
        job.state = JobState.failed
        job.last_error = "Campanha sem snapshot completo do card"
        db.commit()
        return None
    lease_token = str(uuid.uuid4())
    job.account_id = account.id
    job.state = JobState.leased
    job.lease_token = lease_token
    job.lease_until = now + timedelta(seconds=get_settings().account_lease_seconds)
    job.attempt_count += 1
    db.add(
        MessageAttempt(
            job_id=job.id,
            account_id=account.id,
            lease_token=lease_token,
            state="leased",
        )
    )
    db.commit()
    return ClaimedJob(
        id=job.id,
        lease_token=lease_token,
        phone=contact.phone,
        message=materialize_job_message(contact, variant),
        card_text=variant.card_text,
        card_url=variant.card_url,
        card_asset_id=variant.card_asset_id,
        card_show_url=variant.card_show_url,
        contact_name=contact.name,
    )


@router.post("/accounts/{account_id}/jobs/{job_id}/result")
def job_result(
    account_id: uuid.UUID,
    job_id: uuid.UUID,
    worker_id: str,
    payload: JobResult,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    account = owned_account(db, account_id, worker_id)
    job = db.scalar(select(MessageJob).where(MessageJob.id == job_id).with_for_update())
    if not job or job.account_id != account.id or job.lease_token != payload.lease_token:
        raise HTTPException(status_code=409, detail="Lease do job inválido")
    now = datetime.now(timezone.utc)
    if payload.state == "sending":
        if job.state != JobState.leased:
            raise HTTPException(status_code=409, detail="Job não está reservado")
        job.state = JobState.sending
        job.started_at = now
    elif payload.state == "sent":
        if job.state not in (JobState.leased, JobState.sending):
            raise HTTPException(status_code=409, detail="Job não pode ser marcado como enviado")
        job.state = JobState.sent
        job.sent_at = now
        job.provider_message_id = payload.provider_message_id
        job.lease_until = None
        local_day = now.astimezone(ZoneInfo(get_settings().timezone)).date()
        if account.sent_today_date != local_day:
            account.sent_today = 0
            account.sent_today_date = local_day
        account.sent_today += 1
        if get_settings().ro_enabled:
            existing_ro = db.scalar(
                select(RORegistration).where(RORegistration.job_id == job.id)
            )
            if not existing_ro:
                db.add(RORegistration(job_id=job.id, state="pending"))
    else:
        # A failure before `sending` is definitive. Once sending started, the result is uncertain.
        job.state = JobState.review_required if job.state == JobState.sending else JobState.failed
        job.last_error = payload.error or "Falha sem detalhe"
        job.lease_until = None
    db.add(
        MessageAttempt(
            job_id=job.id,
            account_id=account.id,
            lease_token=payload.lease_token,
            state=job.state.value,
            provider_message_id=payload.provider_message_id,
            error=payload.error,
        )
    )
    maybe_complete_campaign(db, job.campaign_id)
    db.commit()
    background_tasks.add_task(
        broker.publish,
        "dashboard",
        {
            "type": "job.status",
            "job_id": str(job.id),
            "campaign_id": str(job.campaign_id),
            "state": job.state.value,
        },
    )
    return {"id": job.id, "state": job.state}


@router.post("/events/ack")
def ack_event(
    payload: AckEvent,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    job = db.scalar(
        select(MessageJob)
        .where(MessageJob.provider_message_id == payload.provider_message_id)
        .with_for_update()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada")
    db.add(
        DeliveryEvent(
            job_id=job.id,
            provider_message_id=payload.provider_message_id,
            ack_level=payload.ack_level,
            payload=payload.payload,
        )
    )
    # Baileys: 0=ERROR, 1=PENDING, 2=SERVER_ACK, 3=DELIVERY_ACK, 4=READ.
    # A server ACK proves acceptance only; it does not prove delivery.
    if payload.ack_level >= 3:
        job.state = JobState.delivered
        job.delivered_at = datetime.now(timezone.utc)
        job.last_error = None
    elif payload.ack_level == 2 and job.state == JobState.review_required:
        # A late server ACK is a successful connector send. Delivery remains
        # exclusive to ACK 3+.
        job.state = JobState.sent
        job.last_error = None
    elif payload.ack_level == 0 and job.state in (
        JobState.sent,
        JobState.review_required,
    ):
        job.state = JobState.failed
        job.last_error = _ack_error_detail(payload)
    maybe_complete_campaign(db, job.campaign_id)
    db.commit()
    background_tasks.add_task(
        broker.publish,
        "dashboard",
        {"type": "job.ack", "job_id": str(job.id), "ack_level": payload.ack_level},
    )
    return {"id": job.id, "state": job.state}
