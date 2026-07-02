from fastapi import APIRouter, Depends, Response, HTTPException
from sqlalchemy.orm import Session

from config import env, env_bool
from models import User
from schemas import LoginRequest, NewUserData
from Methods.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    VALID_USER_ROLES,
    create_access_token,
    get_current_user,
    get_db,
    normalize_role,
    require_roles,
)
from Methods.security import verify_password, get_password_hash

router = APIRouter(tags=["auth"])

COOKIE_NAME = env("AUTH_COOKIE_NAME", "access_token")
COOKIE_DOMAIN = env("AUTH_COOKIE_DOMAIN")
COOKIE_SECURE = env_bool("AUTH_COOKIE_SECURE", True)
COOKIE_SAMESITE = env("AUTH_COOKIE_SAMESITE", "lax")
COOKIE_MAX_AGE_SECONDS = ACCESS_TOKEN_EXPIRE_MINUTES * 60


@router.post("/auth/login")
def login(request: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email).first()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Wrong credentials")

    token_data = {
        "user_id": user.id,
        "name": user.name,
        "surname": user.surname,
        "role": user.role
    }
    token = create_access_token(token_data)

    cookie_options = {
        "key": COOKIE_NAME,
        "value": token,
        "httponly": True,
        "max_age": COOKIE_MAX_AGE_SECONDS,
        "expires": COOKIE_MAX_AGE_SECONDS,
        "samesite": COOKIE_SAMESITE,
        "secure": COOKIE_SECURE,
    }
    if COOKIE_DOMAIN:
        cookie_options["domain"] = COOKIE_DOMAIN

    response.set_cookie(
        **cookie_options,
    )

    return {
        "message": "Logged in successfully",
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "surname": user.surname,
        "role": user.role,
    }


@router.post("/auth/logout")
def logout(response: Response):
    delete_options = {
        "key": COOKIE_NAME,
        "samesite": COOKIE_SAMESITE,
        "secure": COOKIE_SECURE,
    }
    if COOKIE_DOMAIN:
        delete_options["domain"] = COOKIE_DOMAIN
    response.delete_cookie(**delete_options)
    return {"message": "Logged out"}


@router.get("/auth/me")
def get_me(user: User = Depends(get_current_user)):
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "surname": user.surname,
        "role": user.role
    }


@router.post("/auth/register")
def register_user(
    user_data: NewUserData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    role = normalize_role(user_data.role)
    if role not in VALID_USER_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")

    if current_user.role == "mentor" and role != "student":
        raise HTTPException(status_code=403, detail="Mentors can create only students")

    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    hashed_pwd = get_password_hash(user_data.password)

    new_user = User(
        email=user_data.email,
        hashed_password=hashed_pwd,
        name=user_data.name,
        surname=user_data.surname,
        role=role
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "message": "User created successfully",
        "user_id": new_user.id,
        "email": new_user.email,
        "role": new_user.role
    }
