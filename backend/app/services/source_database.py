from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AppSetting, AuditLog, User
from ..schemas import SourceDatabaseUpdate
from ..security import decrypt_json, encrypt_json


SOURCE_DATABASE_KEY = "source_database"


@dataclass(frozen=True)
class SourceDatabaseCredentials:
    server: str
    database: str
    username: str
    password: str


def _stored_values(db: Session) -> dict:
    record = db.get(AppSetting, SOURCE_DATABASE_KEY)
    return dict(record.value) if record else {}


def source_database_settings(db: Session) -> dict:
    values = _stored_values(db)
    environment = get_settings()
    server = values.get("server_old", environment.source_sql_server).strip()
    database = values.get("database_old", environment.source_sql_database).strip()
    username = values.get("username_old", environment.source_sql_username).strip()
    password_configured = bool(
        values.get("password_ciphertext") or environment.source_sql_password
    )
    return {
        "server_old": server,
        "database_old": database,
        "username_old": username,
        "password_configured": password_configured,
        "configured": bool(server and database and username and password_configured),
    }


def save_source_database_settings(
    db: Session, payload: SourceDatabaseUpdate, actor: User
) -> dict:
    server = payload.server_old.strip()
    database = payload.database_old.strip()
    username = payload.username_old.strip()
    if not all((server, database, username)):
        raise ValueError("Preencha SERVER_OLD, DATABASE_OLD e USERNAME_OLD.")

    record = db.get(AppSetting, SOURCE_DATABASE_KEY)
    current = dict(record.value) if record else {}
    password_ciphertext = current.get("password_ciphertext")
    if payload.password_old:
        password_ciphertext = encrypt_json(payload.password_old).decode("ascii")
    if not password_ciphertext and not get_settings().source_sql_password:
        raise ValueError("Preencha PASSWORD_OLD na primeira configuração.")

    if not record:
        record = AppSetting(key=SOURCE_DATABASE_KEY, value={}, updated_by_id=actor.id)
        db.add(record)
    record.value = {
        "server_old": server,
        "database_old": database,
        "username_old": username,
        **({"password_ciphertext": password_ciphertext} if password_ciphertext else {}),
    }
    record.updated_by_id = actor.id
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="settings.source_database.updated",
            entity_type="setting",
            entity_id=SOURCE_DATABASE_KEY,
            details={
                "server_old": server,
                "database_old": database,
                "username_old": username,
                "password_changed": bool(payload.password_old),
            },
        )
    )
    db.commit()
    return source_database_settings(db)


def source_database_credentials(db: Session) -> SourceDatabaseCredentials:
    values = _stored_values(db)
    environment = get_settings()
    password = environment.source_sql_password
    if values.get("password_ciphertext"):
        try:
            password = str(decrypt_json(values["password_ciphertext"].encode("ascii")))
        except (InvalidToken, ValueError, TypeError) as exc:
            raise RuntimeError(
                "Não foi possível ler PASSWORD_OLD. Salve novamente as credenciais."
            ) from exc
    credentials = SourceDatabaseCredentials(
        server=values.get("server_old", environment.source_sql_server).strip(),
        database=values.get("database_old", environment.source_sql_database).strip(),
        username=values.get("username_old", environment.source_sql_username).strip(),
        password=password,
    )
    if not all(
        (credentials.server, credentials.database, credentials.username, credentials.password)
    ):
        raise RuntimeError(
            "Configure SERVER_OLD, DATABASE_OLD, USERNAME_OLD e PASSWORD_OLD "
            "antes de gerar a planilha."
        )
    return credentials
