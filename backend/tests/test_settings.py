from __future__ import annotations

import uuid

from app.models import AppSetting, Role, User
from app.schemas import SettingUpdate
from app.services.settings_service import runtime_settings, save_runtime_settings


def admin(db) -> User:
    user = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash="hash",
        role=Role.admin,
    )
    db.add(user)
    db.commit()
    return user


def test_legacy_daily_cap_payload_is_saved_as_per_chip_cap(db):
    payload = SettingUpdate.model_validate(
        {
            "daily_cap": 35,
            "business_start_hour": 9,
            "business_end_hour": 18,
            "timezone": "America/Sao_Paulo",
        }
    )

    result = save_runtime_settings(db, payload, admin(db))

    assert result["per_chip_daily_cap"] == 35
    assert result["daily_cap"] == 35
    assert db.get(AppSetting, "runtime").value["per_chip_daily_cap"] == 35


def test_legacy_stored_setting_is_normalized_for_old_and_new_frontends(db):
    db.add(
        AppSetting(
            key="runtime",
            value={
                "daily_cap": 42,
                "business_start_hour": 8,
                "business_end_hour": 17,
                "timezone": "America/Sao_Paulo",
            },
        )
    )
    db.commit()

    result = runtime_settings(db)

    assert result["per_chip_daily_cap"] == 42
    assert result["daily_cap"] == 42
