from __future__ import annotations

import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user, require_admin
from ..models import (
    Account,
    AuditLog,
    Campaign,
    CampaignState,
    Contact,
    JobState,
    MessageJob,
    MessageCardAsset,
    User,
)
from ..schemas import MessageCardOut, ReviewDecision, ReviewOut, SettingUpdate
from ..services.message_card import message_card_settings, save_message_card
from ..services.query_export import export_contacts_xlsx
from ..services.settings_service import runtime_settings, save_runtime_settings


router = APIRouter(tags=["operations"])


@router.get("/settings/runtime")
def get_runtime_settings(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return runtime_settings(db)


@router.put("/settings/runtime")
def update_runtime_settings(
    payload: SettingUpdate,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        return save_runtime_settings(db, payload, actor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/settings/message-card", response_model=MessageCardOut)
def get_message_card(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return message_card_settings(db)


@router.put("/settings/message-card", response_model=MessageCardOut)
async def update_message_card(
    text: str = Form(...),
    url: str = Form(...),
    image: UploadFile | None = File(default=None),
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    content = await image.read(5 * 1024 * 1024 + 1) if image else None
    try:
        return save_message_card(
            db,
            text=text,
            url=url,
            image_content=content,
            image_filename=image.filename if image else None,
            actor=actor,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/settings/message-card/image")
def get_message_card_image(
    _: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    card = message_card_settings(db)
    asset = db.get(MessageCardAsset, card["image_asset_id"]) if card["image_asset_id"] else None
    if not asset:
        raise HTTPException(status_code=404, detail="Imagem do card não encontrada")
    return Response(
        asset.content,
        media_type=asset.content_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable", "ETag": asset.sha256},
    )


@router.post("/queries/contacts/export")
def query_export(_: User = Depends(get_current_user)):
    try:
        content = export_contacts_xlsx()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    filename = f"contatos_query_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reviews", response_model=list[ReviewOut])
def reviews(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(MessageJob, Contact, Account)
        .join(Contact, Contact.id == MessageJob.contact_id)
        .outerjoin(Account, Account.id == MessageJob.account_id)
        .where(MessageJob.state.in_([JobState.review_required, JobState.failed]))
        .order_by(MessageJob.started_at)
    ).all()
    return [
        ReviewOut(
            id=job.id,
            campaign_id=job.campaign_id,
            phone=contact.phone,
            account=account.display_name if account else None,
            started_at=job.started_at,
            last_error=job.last_error,
            state=job.state,
        )
        for job, contact, account in rows
    ]


@router.post("/reviews/{job_id}")
def decide_review(
    job_id: uuid.UUID,
    payload: ReviewDecision,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(MessageJob, job_id)
    if not job or job.state not in (JobState.review_required, JobState.failed):
        raise HTTPException(status_code=404, detail="Item de revisão não encontrado")
    if payload.action == "retry":
        campaign = db.get(Campaign, job.campaign_id)
        if not campaign or campaign.state == CampaignState.cancelled:
            raise HTTPException(status_code=409, detail="Campanha não permite retry")
        active = db.scalar(
            select(Campaign.id).where(
                Campaign.state == CampaignState.active,
                Campaign.id != campaign.id,
            )
        )
        if active:
            raise HTTPException(
                status_code=409,
                detail="Finalize ou pause a campanha ativa antes de autorizar o retry.",
            )
        job.state = JobState.pending
        job.account_id = None
        job.lease_token = None
        job.lease_until = None
        job.started_at = None
        job.sent_at = None
        job.provider_message_id = None
        job.last_error = None
        job.scheduled_at = datetime.now().astimezone()
        campaign.state = CampaignState.active
        campaign.finished_at = None
    else:
        job.state = JobState.cancelled
    db.add(
        AuditLog(
            actor_id=actor.id,
            action=f"review.{payload.action}",
            entity_type="job",
            entity_id=str(job.id),
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe uma campanha ativa.") from exc
    return {"id": job.id, "state": job.state}
