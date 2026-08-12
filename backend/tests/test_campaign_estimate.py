from __future__ import annotations

import uuid

from app.models import AppSetting, ImportBatch, ImportState, Role, User
from app.routes.campaigns import estimate
from app.schemas import EstimateRequest


def test_estimate_uses_per_chip_cap_and_returns_legacy_alias(db):
    user = User(
        id=uuid.uuid4(),
        email="operator@example.com",
        password_hash="hash",
        role=Role.operator,
    )
    db.add(user)
    db.flush()
    batch = ImportBatch(
        filename="contatos.csv",
        state=ImportState.ready,
        total_rows=2,
        valid_rows=2,
        invalid_rows=0,
        duplicate_rows=0,
        created_by_id=user.id,
    )
    db.add_all(
        [
            batch,
            AppSetting(
                key="runtime",
                value={
                    "per_chip_daily_cap": 35,
                    "business_start_hour": 9,
                    "business_end_hour": 18,
                    "timezone": "America/Sao_Paulo",
                },
                updated_by_id=user.id,
            ),
        ]
    )
    db.commit()

    result = estimate(
        EstimateRequest(import_id=batch.id, interval_mean_minutes=0),
        user,
        db,
    )

    assert result.valid_contacts == 2
    assert result.per_chip_daily_cap == 35
    assert result.daily_cap == 35
    assert result.daily_capacity == 0
    assert result.warnings == ["Nenhum chip saudável disponível."]
