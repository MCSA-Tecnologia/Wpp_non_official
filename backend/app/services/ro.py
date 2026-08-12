from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..database import SessionLocal
from ..models import Contact, MessageJob, RORegistration


def campaign_code(value: str) -> str:
    digits = "".join(re.findall(r"\d", value or ""))
    return digits or "000000"


def build_record(contact: Contact, job: MessageJob) -> dict:
    settings = get_settings()
    code = campaign_code(contact.campanha)
    sent_at = (job.sent_at or datetime.now(timezone.utc)).astimezone().replace(tzinfo=None)
    stamp = sent_at.strftime("%Y-%m-%dT%H:%M:%S")
    phone = contact.phone.replace("+55", "", 1).replace("+", "")
    partner = f"{settings.ro_parceiro} - {contact.credor}" if contact.credor else settings.ro_parceiro
    return {
        "resumoId": settings.ro_resumo_id,
        "operadorId": settings.ro_operador_id,
        "codigoCampanha": code,
        "campanhaId": int(code) + 2,
        "dataHora": stamp,
        "dataInicio": stamp,
        "dataFim": stamp,
        "pessoaId": int(contact.pessoa_id or 0),
        "origem": settings.ro_origem[:50],
        "historico": f"{partner} ({phone})"[:800],
    }


def process_pending_ro() -> int:
    settings = get_settings()
    if not settings.ro_enabled or not settings.ro_endpoint:
        return 0
    with SessionLocal() as db:
        registrations = list(
            db.scalars(
                select(RORegistration)
                .where(RORegistration.state == "pending")
                .order_by(RORegistration.id)
                .limit(settings.ro_batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        if not registrations:
            return 0
        batch_id = f"RO-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        payload: list[dict] = []
        usable: list[RORegistration] = []
        for registration in registrations:
            job = db.get(MessageJob, registration.job_id)
            contact = db.get(Contact, job.contact_id) if job else None
            if not job or not contact or not contact.pessoa_id:
                registration.state = "error"
                registration.last_error = "Contato sem pessoaId ou job indisponível"
                continue
            registration.state = "processing"
            registration.batch_id = batch_id
            registration.attempts += 1
            payload.append(build_record(contact, job))
            usable.append(registration)
        db.commit()
    if not usable:
        return 0

    try:
        response = httpx.post(
            settings.ro_endpoint,
            json={"registros": payload},
            timeout=settings.ro_timeout_seconds,
        )
        response.raise_for_status()
        success, error = True, None
    except Exception as exc:
        success, error = False, str(exc)[:500]

    with SessionLocal() as db:
        for original in usable:
            registration = db.get(RORegistration, original.id)
            if not registration or registration.state != "processing":
                continue
            registration.state = "success" if success else "review_required"
            registration.registered_at = datetime.now(timezone.utc) if success else None
            registration.last_error = error
        db.commit()
    return len(usable)

