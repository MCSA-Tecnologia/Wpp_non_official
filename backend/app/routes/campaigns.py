from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..events import broker
from ..models import (
    AuditLog,
    Campaign,
    CampaignState,
    ImportBatch,
    JobState,
    MessageCardAsset,
    MessageJob,
    MessageVariant,
    User,
)
from ..schemas import CampaignCreate, CampaignOut, EstimateOut, EstimateRequest
from ..services.campaigns import create_campaign
from ..services.scheduler import (
    assign_campaign_jobs,
    build_schedule,
    campaign_counts,
    healthy_accounts,
)
from ..services.settings_service import require_per_chip_daily_cap, runtime_settings


router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def serialize_campaign(db: Session, campaign: Campaign) -> CampaignOut:
    counts = campaign_counts(db, campaign.id)
    result = CampaignOut.model_validate(campaign)
    result.total = counts.get("total", 0)
    # sent_at is recorded only after the real connector returns a provider id. It
    # therefore remains an accurate attempt count after failure/review/cancellation.
    result.sent = db.scalar(
        select(func.count(MessageJob.id)).where(
            MessageJob.campaign_id == campaign.id,
            MessageJob.sent_at.is_not(None),
        )
    ) or 0
    result.delivered = counts.get("delivered", 0)
    result.failed = counts.get("failed", 0)
    result.review_required = counts.get("review_required", 0)
    return result


def make_plan(
    db: Session,
    import_id: uuid.UUID,
    interval: float,
    jitter: bool = False,
    seed: str = "forecast",
):
    batch = db.get(ImportBatch, import_id)
    if not batch:
        raise ValueError("Importação não encontrada.")
    runtime = runtime_settings(db)
    per_chip_daily_cap = require_per_chip_daily_cap(db)
    accounts = healthy_accounts(db)
    return batch, accounts, build_schedule(
        contact_count=batch.valid_rows,
        accounts=accounts,
        requested_interval_minutes=interval,
        per_chip_daily_cap=per_chip_daily_cap,
        timezone_name=runtime["timezone"],
        start_hour=int(runtime["business_start_hour"]),
        end_hour=int(runtime["business_end_hour"]),
        jitter=jitter,
        random_seed=str(seed),
    )


def prepare_campaign_start(db: Session, campaign: Campaign, actor: User):
    active = db.scalar(
        select(Campaign).where(Campaign.state == CampaignState.active, Campaign.id != campaign.id)
    )
    if active:
        raise PermissionError("Já existe uma campanha ativa.")
    if campaign.state not in (CampaignState.draft, CampaignState.scheduled, CampaignState.paused):
        raise PermissionError("A campanha não pode ser iniciada neste estado.")
    variant = db.scalar(
        select(MessageVariant).where(
            MessageVariant.campaign_id == campaign.id,
            MessageVariant.active.is_(True),
        )
    )
    if (
        not variant
        or not variant.card_text
        or not variant.card_url
        or not variant.card_asset_id
        or not db.get(MessageCardAsset, variant.card_asset_id)
    ):
        raise ValueError("A campanha não possui um snapshot completo do card da mensagem.")

    accounts = healthy_accounts(db)
    runtime = runtime_settings(db)
    jobs_total = campaign_counts(db, campaign.id).get("total", 0)
    plan = build_schedule(
        contact_count=jobs_total,
        accounts=accounts,
        requested_interval_minutes=campaign.interval_mean_minutes,
        per_chip_daily_cap=campaign.per_chip_daily_cap_snapshot,
        timezone_name=runtime["timezone"],
        start_hour=int(runtime["business_start_hour"]),
        end_hour=int(runtime["business_end_hour"]),
        jitter=True,
        random_seed=str(campaign.id),
    )
    if not plan.entries:
        raise ValueError(plan.warnings[0] if plan.warnings else "Não foi possível criar a agenda.")
    assign_campaign_jobs(db, campaign.id, plan)
    campaign.state = CampaignState.active
    campaign.effective_interval_minutes = plan.effective_interval_minutes
    campaign.estimated_start_at = plan.start_at
    campaign.estimated_finish_at = plan.finish_at
    campaign.started_at = campaign.started_at or datetime.now(timezone.utc)
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="campaign.started",
            entity_type="campaign",
            entity_id=str(campaign.id),
            details={"healthy_accounts": len(accounts), "real_send_confirmed": True},
        )
    )
    return accounts, plan


@router.get("", response_model=list[CampaignOut])
def list_campaigns(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaigns = list(db.scalars(select(Campaign).order_by(Campaign.created_at.desc()).limit(100)))
    return [serialize_campaign(db, campaign) for campaign in campaigns]


@router.post("/estimate", response_model=EstimateOut)
def estimate(
    payload: EstimateRequest,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        batch, accounts, plan = make_plan(db, payload.import_id, payload.interval_mean_minutes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    start = plan.start_at
    finish = plan.finish_at
    duration = (finish - start).total_seconds() / 60 if start and finish else 0
    runtime = runtime_settings(db)
    per_chip_daily_cap = require_per_chip_daily_cap(db)
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo(runtime["timezone"])).date()
    remaining_capacity = sum(
        max(
            0,
            per_chip_daily_cap
            - (account.sent_today if account.sent_today_date == local_day else 0),
        )
        for account in accounts
    )
    return EstimateOut(
        valid_contacts=batch.valid_rows,
        healthy_accounts=len(accounts),
        contacts_per_account=plan.contacts_per_account,
        effective_interval_minutes=plan.effective_interval_minutes,
        estimated_start_at=start,
        estimated_finish_at=finish,
        duration_minutes=round(duration, 1),
        spills_to_next_day=bool(plan.warnings),
        per_chip_daily_cap=per_chip_daily_cap,
        daily_cap=per_chip_daily_cap,
        daily_capacity=len(accounts) * per_chip_daily_cap,
        remaining_capacity_today=remaining_capacity,
        warnings=plan.warnings,
    )


@router.post("", response_model=CampaignOut, status_code=201)
def create(
    payload: CampaignCreate,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.confirmed_real_send:
        raise HTTPException(status_code=422, detail="Confirme explicitamente o disparo real.")
    try:
        campaign = create_campaign(db, payload, actor, require_per_chip_daily_cap(db))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return serialize_campaign(db, campaign)


@router.post("/confirm-and-start", response_model=CampaignOut, status_code=201)
async def confirm_and_start(
    payload: CampaignCreate,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.confirmed_real_send:
        raise HTTPException(status_code=422, detail="Confirme explicitamente o disparo real.")
    try:
        campaign = create_campaign(
            db,
            payload,
            actor,
            require_per_chip_daily_cap(db),
            commit=False,
        )
        prepare_campaign_start(db, campaign, actor)
        db.commit()
        db.refresh(campaign)
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe uma campanha ativa.") from exc
    await broker.publish("dashboard", {"type": "campaign.started", "campaign_id": str(campaign.id)})
    return serialize_campaign(db, campaign)


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(
    campaign_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    return serialize_campaign(db, campaign)


@router.post("/{campaign_id}/start", response_model=CampaignOut)
async def start_campaign(
    campaign_id: uuid.UUID,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    try:
        prepare_campaign_start(db, campaign, actor)
        db.commit()
        db.refresh(campaign)
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe uma campanha ativa.") from exc
    await broker.publish("dashboard", {"type": "campaign.started", "campaign_id": str(campaign.id)})
    return serialize_campaign(db, campaign)


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(
    campaign_id: uuid.UUID,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.get(Campaign, campaign_id)
    if not campaign or campaign.state != CampaignState.active:
        raise HTTPException(status_code=409, detail="Campanha não está ativa")
    campaign.state = CampaignState.paused
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="campaign.paused",
            entity_type="campaign",
            entity_id=str(campaign.id),
        )
    )
    db.commit()
    await broker.publish("dashboard", {"type": "campaign.paused", "campaign_id": str(campaign.id)})
    return serialize_campaign(db, campaign)


@router.post("/{campaign_id}/resume", response_model=CampaignOut)
async def resume_campaign(
    campaign_id: uuid.UUID,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.get(Campaign, campaign_id)
    if not campaign or campaign.state != CampaignState.paused:
        raise HTTPException(status_code=409, detail="Campanha não está pausada")
    if db.scalar(select(Campaign).where(Campaign.state == CampaignState.active)):
        raise HTTPException(status_code=409, detail="Já existe uma campanha ativa")
    campaign.state = CampaignState.active
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="campaign.resumed",
            entity_type="campaign",
            entity_id=str(campaign.id),
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Já existe uma campanha ativa.") from exc
    await broker.publish("dashboard", {"type": "campaign.resumed", "campaign_id": str(campaign.id)})
    return serialize_campaign(db, campaign)


@router.post("/{campaign_id}/cancel", response_model=CampaignOut)
async def cancel_campaign(
    campaign_id: uuid.UUID,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campanha não encontrada")
    campaign.state = CampaignState.cancelled
    db.execute(
        update(MessageJob)
        .where(
            MessageJob.campaign_id == campaign.id,
            MessageJob.state.in_([JobState.pending, JobState.scheduled, JobState.leased]),
        )
        .values(state=JobState.cancelled, lease_token=None, lease_until=None)
    )
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="campaign.cancelled",
            entity_type="campaign",
            entity_id=str(campaign.id),
        )
    )
    db.commit()
    await broker.publish(
        "dashboard", {"type": "campaign.cancelled", "campaign_id": str(campaign.id)}
    )
    return serialize_campaign(db, campaign)
