from __future__ import annotations

import uuid

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dependencies import get_current_user, require_admin
from ..models import AuditLog, User
from ..schemas import LoginRequest, PasswordChange, UserCreate, UserOut
from ..security import create_token, decode_token, hash_password, verify_password


router = APIRouter(prefix="/auth", tags=["auth"])


def set_auth_cookies(response: Response, user: User) -> None:
    settings = get_settings()
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    response.set_cookie(
        "access_token",
        create_token(user.id, user.role.value, "access"),
        max_age=settings.access_token_minutes * 60,
        **common,
    )
    response.set_cookie(
        "refresh_token",
        create_token(user.id, user.role.value, "refresh"),
        max_age=settings.refresh_token_days * 86400,
        **common,
    )


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower().strip()))
    if not user or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos")
    set_auth_cookies(response, user)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="auth.login",
            entity_type="user",
            entity_id=str(user.id),
        )
    )
    db.commit()
    return user


@router.post("/refresh", response_model=UserOut)
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token ausente")
    try:
        payload = decode_token(refresh_token, "refresh")
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="Sessão expirada") from exc
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Usuário indisponível")
    set_auth_cookies(response, user)
    return user


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return list(db.scalars(select(User).order_by(User.email)))


@router.post("/change-password", status_code=204)
def change_password(
    payload: PasswordChange,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="Senha atual incorreta")
    user.password_hash = hash_password(payload.new_password)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="auth.password_changed",
            entity_type="user",
            entity_id=str(user.id),
        )
    )
    db.commit()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = payload.email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado")
    user = User(email=email, password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    db.flush()
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="user.created",
            entity_type="user",
            entity_id=str(user.id),
            details={"email": email, "role": payload.role.value},
        )
    )
    db.commit()
    db.refresh(user)
    return user
