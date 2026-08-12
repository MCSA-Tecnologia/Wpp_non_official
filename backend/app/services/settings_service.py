from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AppSetting, User
from ..schemas import SettingUpdate


RUNTIME_KEY = "runtime"


def runtime_settings(db: Session) -> dict:
    configured = db.get(AppSetting, RUNTIME_KEY)
    if configured:
        values = dict(configured.value)
        cap = values.get("per_chip_daily_cap", values.get("daily_cap"))
        return {
            "per_chip_daily_cap": cap,
            "daily_cap": cap,
            "business_start_hour": values.get("business_start_hour", 9),
            "business_end_hour": values.get("business_end_hour", 18),
            "timezone": values.get("timezone", "America/Sao_Paulo"),
        }
    settings = get_settings()
    return {
        "per_chip_daily_cap": settings.per_chip_daily_cap,
        "daily_cap": settings.per_chip_daily_cap,
        "business_start_hour": settings.business_start_hour,
        "business_end_hour": settings.business_end_hour,
        "timezone": settings.timezone,
    }


def save_runtime_settings(db: Session, payload: SettingUpdate, actor: User) -> dict:
    if payload.business_end_hour <= payload.business_start_hour:
        raise ValueError("O fim da janela deve ser posterior ao início.")
    record = db.get(AppSetting, RUNTIME_KEY)
    if not record:
        record = AppSetting(key=RUNTIME_KEY, value={}, updated_by_id=actor.id)
        db.add(record)
    record.value = payload.model_dump()
    record.updated_by_id = actor.id
    db.commit()
    return runtime_settings(db)


def require_per_chip_daily_cap(db: Session) -> int:
    value = runtime_settings(db).get("per_chip_daily_cap")
    if value is None or int(value) <= 0:
        raise ValueError("Configure o teto diário por chip antes de criar ou iniciar campanhas.")
    return int(value)
