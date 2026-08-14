from __future__ import annotations

import hashlib
import io
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from ..models import AppSetting, AuditLog, MessageCardAsset, User

MESSAGE_CARD_KEY = "message_card"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_CARD_EDGE = 600


def _validate_text(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Informe o texto do card.")
    if len(value) > 120:
        raise ValueError("O texto da chamada para ação deve ter no máximo 120 caracteres.")
    return value


def _validate_url(value: str) -> str:
    value = value.strip()
    if len(value) > 2048:
        raise ValueError("O link deve ter no máximo 2.048 caracteres.")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Informe uma URL HTTPS válida para o link.")
    if parsed.username or parsed.password:
        raise ValueError("A URL do link não pode conter credenciais.")
    return value


def normalize_card_image(content: bytes) -> bytes:
    if not content:
        raise ValueError("Selecione uma imagem JPG ou PNG.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("A imagem da mensagem deve ter no máximo 5 MB.")
    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in {"JPEG", "PNG"}:
                raise ValueError("A imagem da mensagem deve estar em formato JPG ou PNG.")
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((MAX_CARD_EDGE, MAX_CARD_EDGE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=84, optimize=True, progressive=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Não foi possível ler a imagem. Envie um JPG ou PNG válido.") from exc


def _revision(text: str, url: str, asset_id: uuid.UUID, show_url: bool) -> str:
    payload = json.dumps(
        {
            "text": text,
            "url": url,
            "image_asset_id": str(asset_id),
            "show_url": show_url,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def message_card_settings(db: Session) -> dict:
    record = db.get(AppSetting, MESSAGE_CARD_KEY)
    values = dict(record.value) if record else {}
    text = str(values.get("text") or "")
    url = str(values.get("url") or "")
    try:
        asset_id = uuid.UUID(str(values.get("image_asset_id")))
    except (TypeError, ValueError):
        asset_id = None
    asset = db.get(MessageCardAsset, asset_id) if asset_id else None
    show_url = bool(values.get("show_url", True))
    configured = bool(text and url and asset)
    revision = _revision(text, url, asset.id, show_url) if configured and asset else ""
    return {
        "text": text,
        "url": url,
        "image_asset_id": asset.id if asset else None,
        "image_url": (
            f"/api/v1/settings/message-card/image?v={asset.id}" if asset else None
        ),
        "show_url": show_url,
        "revision": revision,
        "configured": configured,
        "updated_at": record.updated_at if record else None,
    }


def require_message_card(db: Session, expected_revision: str = "") -> dict:
    card = message_card_settings(db)
    if not card["configured"]:
        raise ValueError(
            "Configure texto, imagem e link antes de iniciar a campanha."
        )
    if not expected_revision or expected_revision != card["revision"]:
        raise ValueError(
            "O card foi alterado. Atualize a página e revise o disparo novamente."
        )
    return card


def save_message_card(
    db: Session,
    *,
    text: str,
    url: str,
    image_content: bytes | None,
    image_filename: str | None,
    actor: User,
    show_url: bool = True,
) -> dict:
    normalized_text = _validate_text(text)
    normalized_url = _validate_url(url)
    current = message_card_settings(db)
    asset_id = current["image_asset_id"]

    if image_content is not None:
        normalized_image = normalize_card_image(image_content)
        asset = MessageCardAsset(
            filename=Path(image_filename or "card.jpg").name[:255],
            content_type="image/jpeg",
            content=normalized_image,
            byte_size=len(normalized_image),
            sha256=hashlib.sha256(normalized_image).hexdigest(),
        )
        db.add(asset)
        db.flush()
        asset_id = asset.id
    if not asset_id:
        raise ValueError("Selecione a imagem do card.")
    record = db.get(AppSetting, MESSAGE_CARD_KEY)
    if not record:
        record = AppSetting(key=MESSAGE_CARD_KEY, value={}, updated_by_id=actor.id)
        db.add(record)
    record.value = {
        "text": normalized_text,
        "url": normalized_url,
        "image_asset_id": str(asset_id) if asset_id else None,
        "show_url": show_url,
    }
    record.updated_by_id = actor.id
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="settings.message_card.updated",
            entity_type="setting",
            entity_id=MESSAGE_CARD_KEY,
            details={
                "image_asset_id": str(asset_id) if asset_id else None,
                "url": normalized_url,
                "show_url": show_url,
            },
        )
    )
    db.commit()
    return message_card_settings(db)
