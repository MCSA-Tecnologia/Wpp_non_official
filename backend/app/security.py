from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

from .config import get_settings


password_hasher = PasswordHasher()


def validate_runtime_secrets() -> None:
    settings = get_settings()
    errors: list[str] = []
    unsafe_markers = ("change-me", "replace-this", "replace-with")
    if len(settings.jwt_secret) < 32 or any(
        marker in settings.jwt_secret for marker in unsafe_markers
    ):
        errors.append("AUTOWPP_JWT_SECRET deve ser aleatório e ter pelo menos 32 caracteres")
    if len(settings.worker_token) < 32 or any(
        marker in settings.worker_token for marker in unsafe_markers
    ):
        errors.append("AUTOWPP_WORKER_TOKEN deve ser um token aleatório independente")
    if not settings.encryption_key:
        errors.append("AUTOWPP_ENCRYPTION_KEY é obrigatória")
    else:
        try:
            Fernet(settings.encryption_key.encode())
        except (TypeError, ValueError):
            errors.append("AUTOWPP_ENCRYPTION_KEY não é uma chave Fernet válida")
    if any(marker in settings.bootstrap_admin_password for marker in unsafe_markers):
        errors.append("AUTOWPP_BOOTSTRAP_ADMIN_PASSWORD não pode usar o valor padrão")
    if errors:
        raise RuntimeError("Configuração insegura: " + "; ".join(errors))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except VerifyMismatchError:
        return False


def create_token(user_id: uuid.UUID, role: str, token_type: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    lifetime = (
        timedelta(minutes=settings.access_token_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_days)
    )
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str = "access") -> dict:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Unexpected token type")
    return payload


def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.encryption_key:
        raise RuntimeError("AUTOWPP_ENCRYPTION_KEY é obrigatória")
    return Fernet(settings.encryption_key.encode())


def encrypt_json(value) -> bytes:
    return _fernet().encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def decrypt_json(value: bytes):
    return json.loads(_fernet().decrypt(value).decode("utf-8"))
