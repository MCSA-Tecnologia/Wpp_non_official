from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app.models import AppSetting, AuditLog, Role, User
from app.schemas import SourceDatabaseUpdate
from app.services import source_database


def admin(db) -> User:
    user = User(
        id=uuid.uuid4(),
        email="source-admin@example.com",
        password_hash="hash",
        role=Role.admin,
    )
    db.add(user)
    db.commit()
    return user


def environment(**overrides):
    values = {
        "source_sql_server": "",
        "source_sql_database": "",
        "source_sql_username": "",
        "source_sql_password": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_source_database_password_is_encrypted_and_never_returned(db, monkeypatch):
    monkeypatch.setattr(source_database, "get_settings", lambda: environment())
    monkeypatch.setattr(source_database, "encrypt_json", lambda value: b"encrypted-password")
    monkeypatch.setattr(source_database, "decrypt_json", lambda value: "secret")

    result = source_database.save_source_database_settings(
        db,
        SourceDatabaseUpdate(
            server_old="sql.example",
            database_old="Candiotto_STD",
            username_old="readonly",
            password_old="secret",
        ),
        admin(db),
    )

    assert result == {
        "server_old": "sql.example",
        "database_old": "Candiotto_STD",
        "username_old": "readonly",
        "password_configured": True,
        "configured": True,
    }
    stored = db.get(AppSetting, source_database.SOURCE_DATABASE_KEY).value
    assert stored["password_ciphertext"] == "encrypted-password"
    assert "secret" not in str(stored)
    assert db.scalar(
        select(AuditLog).where(AuditLog.action == "settings.source_database.updated")
    )
    credentials = source_database.source_database_credentials(db)
    assert credentials.password == "secret"


def test_blank_password_preserves_the_current_encrypted_value(db, monkeypatch):
    monkeypatch.setattr(source_database, "get_settings", lambda: environment())
    monkeypatch.setattr(source_database, "encrypt_json", lambda value: b"first-password")
    actor = admin(db)
    source_database.save_source_database_settings(
        db,
        SourceDatabaseUpdate(
            server_old="server-a",
            database_old="database-a",
            username_old="user-a",
            password_old="secret",
        ),
        actor,
    )

    source_database.save_source_database_settings(
        db,
        SourceDatabaseUpdate(
            server_old="server-b",
            database_old="database-b",
            username_old="user-b",
            password_old="",
        ),
        actor,
    )

    stored = db.get(AppSetting, source_database.SOURCE_DATABASE_KEY).value
    assert stored["password_ciphertext"] == "first-password"
    assert stored["server_old"] == "server-b"
