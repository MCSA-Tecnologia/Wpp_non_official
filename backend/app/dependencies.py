from __future__ import annotations

import uuid

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Role, User
from .security import decode_token


def get_current_user(
    access_token: str | None = Cookie(default=None), db: Session = Depends(get_db)
) -> User:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = decode_token(access_token)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc
    user = db.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="User unavailable")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def require_worker(x_worker_token: str = Header(default="")) -> None:
    if x_worker_token != get_settings().worker_token:
        raise HTTPException(status_code=403, detail="Invalid worker token")

