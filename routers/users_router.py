from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user, require_admin, require_staff
from models import User
from schemas import NewUserData, UpdateUserData
from Methods.auth import (
    VALID_USER_ROLES,
    get_db,
    get_current_user,
    normalize_role,
    require_roles,
)
from Methods.security import get_password_hash

router = APIRouter(prefix="/users", tags=["users"])



@router.post("/")
def create_user(
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

    new_user = User(
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
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


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    user_data: UpdateUserData,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    actor_role = normalize_role(current_user.role)
    target_role = normalize_role(user.role)

    if actor_role == "student" and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    if actor_role == "mentor":
        if target_role != "student":
            raise HTTPException(status_code=403, detail="Mentors can edit only students")

    if actor_role == "teacher":
        if target_role != "student":
            raise HTTPException(status_code=403, detail="Teachers can edit only students")

    if user_data.email is not None:
        existing_email = db.query(User).filter(User.email == user_data.email, User.id != user_id).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="Email already in use")
        user.email = user_data.email

    if user_data.name is not None:
        user.name = user_data.name

    if user_data.surname is not None:
        user.surname = user_data.surname

    if user_data.password is not None:
        user.hashed_password = get_password_hash(user_data.password)

    if user_data.role is not None:
        role = normalize_role(user_data.role)
        if role not in VALID_USER_ROLES:
            raise HTTPException(status_code=400, detail="Invalid role")

        if current_user.role == "admin":
            user.role = role
        elif current_user.role == "mentor":
            raise HTTPException(status_code=403, detail="Mentors cannot change user roles")
        elif current_user.role == "teacher":
            if role != "student":
                raise HTTPException(status_code=403, detail="Teachers cannot change role to this value")

    db.commit()
    db.refresh(user)

    return {
        "message": "User updated successfully",
        "user_id": user.id
    }


