from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError

from Methods.auth import AUTH_COOKIE_NAME, get_db
from config import env
from models import User

JWT_SECRET_KEY = env("JWT_SECRET_KEY", required=True)
JWT_ALGORITHM = env("JWT_ALGORITHM", "HS256")


@dataclass(frozen=True)
class AuthUser:
    id: int
    role: str


def normalize_role(role: str | None) -> str:
    return (role or "").strip().lower()


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def _user_id_from_payload(payload: dict) -> int:
    raw_user_id = payload.get("sub", payload.get("user_id"))
    if raw_user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        return int(raw_user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthUser:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        token = _extract_bearer_token(request.headers.get("Authorization"))

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = _decode_access_token(token)
    user_id = _user_id_from_payload(payload)

    row = (
        db.query(User.id, User.role)
        .filter(User.id == user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=401, detail="User not found")

    return AuthUser(id=row.id, role=normalize_role(row.role))


def is_admin_or_mentor(role: str | None) -> bool:
    return normalize_role(role) in ("admin", "mentor")


def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if normalize_role(user.role) != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return user


def require_admin_or_mentor(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if normalize_role(user.role) not in ("admin", "mentor"):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return user


def require_staff(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if normalize_role(user.role) not in ("admin", "mentor", "teacher"):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return user
