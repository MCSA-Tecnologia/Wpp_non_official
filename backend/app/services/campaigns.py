from __future__ import annotations

import re
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AuditLog,
    Campaign,
    Contact,
    ImportBatch,
    ImportRow,
    ImportState,
    JobState,
    MessageJob,
    MessageVariant,
    User,
)
from ..schemas import CampaignCreate
from .imports import canonical_row, map_columns
from .message_card import require_message_card
from .message_variations import validate_message_variations

PLACEHOLDER_PATTERN = re.compile(
    r"(?P<name>(?i:NOME\s*_?\s*DO\s*_?\s*CLIENTE))|(?P<creditor>\bCREDOR\b)"
)
UNDERLINE_PATTERN = re.compile(r"<u>(.*?)</u>", re.DOTALL)


def _short_customer_name(name: str) -> str:
    parts = name.split()
    if not parts:
        return ""
    selected = parts if len(parts) <= 2 else (parts[0], parts[-1])
    return " ".join(part.title() for part in selected)


def _clean_empty_name_spacing(message: str) -> str:
    # Clean only horizontal whitespace, preserving the operator's line breaks.
    message = re.sub(r"[^\S\r\n]+([,.;:!?])", r"\1", message)
    message = re.sub(r"[^\S\r\n]{2,}", " ", message)
    message = re.sub(r"[^\S\r\n]+$", "", message, flags=re.MULTILINE)
    return re.sub(r"^[^\S\r\n]+", "", message, flags=re.MULTILINE)


def _render_underlines(message: str) -> str:
    def underline(match: re.Match[str]) -> str:
        return "".join(
            character if character.isspace() else f"{character}\u0332"
            for character in match.group(1)
        )

    return UNDERLINE_PATTERN.sub(underline, message)


def render_message(template: str, name: str, creditor: str = "") -> str:
    short_name = _short_customer_name(name)

    def replacement(match: re.Match[str]) -> str:
        return short_name if match.lastgroup == "name" else creditor

    # A single substitution pass prevents placeholders inside spreadsheet values
    # from being interpreted recursively.
    rendered = PLACEHOLDER_PATTERN.sub(replacement, template)
    rendered = _render_underlines(rendered)
    return _clean_empty_name_spacing(rendered) if not short_name else rendered


def create_campaign(
    db: Session,
    payload: CampaignCreate,
    actor: User,
    per_chip_daily_cap: int,
    *,
    commit: bool = True,
) -> Campaign:
    batch = db.get(ImportBatch, payload.import_id)
    if not batch or batch.state != ImportState.ready:
        raise ValueError("Importação não encontrada ou ainda não está pronta.")
    if batch.created_by_id != actor.id and actor.role.value != "admin":
        raise PermissionError("A importação pertence a outro usuário.")

    card = require_message_card(db, payload.card_revision)

    campaign = Campaign(
        name=payload.name,
        source_import_id=batch.id,
        interval_mean_minutes=payload.interval_mean_minutes,
        business_start_hour=get_settings().business_start_hour,
        business_end_hour=get_settings().business_end_hour,
        timezone=get_settings().timezone,
        per_chip_daily_cap_snapshot=per_chip_daily_cap,
        created_by_id=actor.id,
    )
    db.add(campaign)
    db.flush()
    message_bodies = [
        payload.message,
        *validate_message_variations(payload.message, payload.message_variations),
    ]
    variants: list[MessageVariant] = []
    for body in message_bodies:
        variant = MessageVariant(
            campaign_id=campaign.id,
            body=body,
            card_text=card["text"],
            card_url=card["url"],
            card_asset_id=card["image_asset_id"],
            card_show_url=card["show_url"],
            weight=100,
            active=True,
        )
        db.add(variant)
        variants.append(variant)
    db.flush()

    valid_rows = list(
        db.scalars(
            select(ImportRow)
            .where(ImportRow.batch_id == batch.id, ImportRow.valid.is_(True))
            .order_by(ImportRow.row_number)
        )
    )
    if not valid_rows:
        raise ValueError("A importação não contém contatos válidos.")
    columns = map_columns_from_raw(valid_rows[0].raw_data)
    for row in valid_rows:
        values = canonical_row(row.raw_data, columns)
        contact = Contact(
            campaign_id=campaign.id,
            phone=row.normalized_phone or "",
            name=values.get("name", ""),
            pessoa_id=values.get("pessoa_id") or None,
            email=values.get("email", ""),
            observacao=values.get("observacao", ""),
            credor=values.get("credor", ""),
            campanha=values.get("campanha", ""),
            extra_data=row.raw_data,
        )
        db.add(contact)
        db.flush()
        db.add(
            MessageJob(
                idempotency_key=f"{campaign.id}:{contact.id}",
                campaign_id=campaign.id,
                contact_id=contact.id,
                variant_id=secrets.choice(variants).id,
                state=JobState.pending,
            )
        )
    # Production sessions disable autoflush. Persist every job before the
    # scheduler counts and assigns them; otherwise the final imported row can
    # miss scheduled_at and bypass the per-chip cadence.
    db.flush()
    batch.state = ImportState.consumed
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="campaign.created",
            entity_type="campaign",
            entity_id=str(campaign.id),
            details={
                "contacts": len(valid_rows),
                "source": batch.filename,
                "variation_count": len(payload.message_variations),
                "message_pool_size": len(variants),
            },
        )
    )
    if commit:
        db.commit()
    db.refresh(campaign)
    return campaign


def map_columns_from_raw(raw: dict) -> dict[str, str | None]:
    # Reuse the importer mapping without constructing persistent DataFrames.
    import pandas as pd

    return map_columns(pd.DataFrame(columns=list(raw.keys())))


def materialize_job_message(contact: Contact, variant: MessageVariant) -> str:
    return render_message(variant.body, contact.name, contact.credor)
