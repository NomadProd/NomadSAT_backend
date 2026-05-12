from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import jwt, JWTError
from config import env, env_int
from database import SessionLocal
from models import User

SECRET_KEY = env("JWT_SECRET_KEY", required=True)
ALGORITHM = env("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 150)
AUTH_COOKIE_NAME = env("AUTH_COOKIE_NAME", "access_token")
VALID_USER_ROLES = {"student", "teacher", "mentor", "admin"}


def normalize_role(role: str | None) -> str:
    return (role or "").strip().lower()


def role_is_allowed(user_role: str, roles: list[str]) -> bool:
    role = normalize_role(user_role)
    allowed_roles = {normalize_role(allowed_role) for allowed_role in roles}
    if role in allowed_roles:
        return True
    return role == "mentor" and "admin" in allowed_roles


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_access_token(data: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
):
    access_token = request.cookies.get(AUTH_COOKIE_NAME)
    if not access_token:
        raise HTTPException(status_code=401, detail="Кука с токеном не найдена")
    
    payload = decode_token(access_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Токен невалиден")
    
    user_id = payload.get("user_id")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Юзер не найден в базе")
    return user

def require_roles(roles: list[str]):
    def checker(user: User = Depends(get_current_user)):
        if not role_is_allowed(user.role, roles):
            raise HTTPException(status_code=403)
        return user
    return checker
