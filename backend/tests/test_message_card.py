from __future__ import annotations

import uuid

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.models import AuditLog, Campaign, MessageCardAsset, MessageVariant, Role, User
from app.services.message_card import (
    MAX_UPLOAD_BYTES,
    normalize_card_image,
    require_message_card,
    save_message_card,
)


def make_admin(db) -> User:
    actor = User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4()}@example.com",
        password_hash="hash",
        role=Role.admin,
    )
    db.add(actor)
    db.commit()
    return actor


def test_card_image_is_normalized_to_bounded_jpeg(png_1x1):
    normalized = normalize_card_image(png_1x1)

    assert normalized.startswith(b"\xff\xd8")
    with Image.open(__import__("io").BytesIO(normalized)) as image:
        assert image.format == "JPEG"
        assert image.width <= 600
        assert image.height <= 600


@pytest.mark.parametrize(
    ("text", "url", "expected"),
    [
        ("", "https://example.com", "texto"),
        ("Card", "http://example.com", "HTTPS"),
        ("Card", "https://user:password@example.com", "credenciais"),
    ],
)
def test_card_rejects_invalid_required_fields(db, png_1x1, text, url, expected):
    with pytest.raises(ValueError, match=expected):
        save_message_card(
            db,
            text=text,
            url=url,
            image_content=png_1x1,
            image_filename="card.png",
            actor=make_admin(db),
        )


def test_card_rejects_oversized_or_invalid_image():
    with pytest.raises(ValueError, match="5 MB"):
        normalize_card_image(b"x" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(ValueError, match="JPG ou PNG válido"):
        normalize_card_image(b"not-an-image")


def test_card_save_is_audited_and_old_campaign_snapshot_is_immutable(
    db, png_1x1, configure_message_card
):
    actor = make_admin(db)
    original = configure_message_card(actor)
    campaign = Campaign(
        name="Snapshot",
        per_chip_daily_cap_snapshot=35,
        created_by_id=actor.id,
    )
    db.add(campaign)
    db.flush()
    snapshot = MessageVariant(
        campaign_id=campaign.id,
        body="Mensagem",
        card_text=original["text"],
        card_url=original["url"],
        card_asset_id=original["image_asset_id"],
        card_show_url=original["show_url"],
    )
    db.add(snapshot)
    db.commit()
    replacement = save_message_card(
        db,
        text="Novo card",
        url="https://example.org/novo",
        image_content=png_1x1,
        image_filename="novo.png",
        actor=actor,
        show_url=False,
    )

    assert replacement["configured"] is True
    assert replacement["revision"] != original["revision"]
    assert replacement["image_asset_id"] != original["image_asset_id"]
    db.refresh(snapshot)
    assert snapshot.card_text == original["text"]
    assert snapshot.card_url == original["url"]
    assert snapshot.card_asset_id == original["image_asset_id"]
    assert snapshot.card_show_url is True
    assert replacement["show_url"] is False
    assert db.scalar(select(func.count(MessageCardAsset.id))) == 2
    assert db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "settings.message_card.updated"
        )
    ) == 2


def test_link_visibility_is_versioned_without_replacing_the_image(
    db, configure_message_card
):
    actor = make_admin(db)
    original = configure_message_card(actor)

    embedded = save_message_card(
        db,
        text=original["text"],
        url=original["url"],
        image_content=None,
        image_filename=None,
        actor=actor,
        show_url=False,
    )

    assert original["show_url"] is True
    assert embedded["show_url"] is False
    assert embedded["image_asset_id"] == original["image_asset_id"]
    assert embedded["revision"] != original["revision"]


def test_campaign_confirmation_requires_current_card_revision(db, configure_message_card):
    actor = make_admin(db)
    card = configure_message_card(actor)

    assert require_message_card(db, card["revision"])["url"] == card["url"]
    with pytest.raises(ValueError, match="alterado"):
        require_message_card(db, "stale")
