from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user, normalize_role, require_admin, require_admin_or_mentor, require_staff
from Methods.auth import get_db
from models import (
    Assignment,
    Attendance,
    ClassEnrollment,
    HomeworkResult,
    MockResult,
    Session as ClassSession,
    User,
)

router = APIRouter(tags=["users"])


def serialize_user(user: User) -> dict:
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "surname": user.surname,
        "role": user.role,
    }


@router.get("/users/me")
def get_current_user_profile(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return serialize_user(user)


@router.get("/users/all")
def list_users_legacy(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    users = db.query(User).order_by(User.id.asc()).all()
    return [serialize_user(user) for user in users]


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    users = db.query(User).order_by(User.id.asc()).all()
    return [serialize_user(user) for user in users]


@router.get("/users/students")
def list_students(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    students = db.query(User).filter(User.role == "student").order_by(User.id.asc()).all()
    return [
        {
            "user_id": student.id,
            "name": student.name,
            "surname": student.surname,
        }
        for student in students
    ]


@router.get("/users/teachers")
def list_teachers(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    teachers = db.query(User).filter(User.role == "teacher").order_by(User.id.asc()).all()
    return [
        {
            "user_id": teacher.id,
            "email": teacher.email,
            "name": teacher.name,
            "surname": teacher.surname,
            "role": teacher.role,
        }
        for teacher in teachers
    ]


@router.get("/users/{user_id}")
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return serialize_user(user)


def delete_user_dependents(db: Session, user_id: int) -> None:
    assignment_ids = [
        row[0]
        for row in db.query(Assignment.id).filter(Assignment.student_id == user_id).all()
    ]

    if assignment_ids:
        db.query(MockResult).filter(
            MockResult.assignment_id.in_(assignment_ids)
        ).delete(synchronize_session=False)
        db.query(HomeworkResult).filter(
            HomeworkResult.assignment_id.in_(assignment_ids)
        ).delete(synchronize_session=False)
        db.query(Assignment).filter(Assignment.student_id == user_id).delete(
            synchronize_session=False
        )

    db.query(Attendance).filter(Attendance.student_id == user_id).delete(
        synchronize_session=False
    )
    db.query(MockResult).filter(MockResult.student_id == user_id).delete(
        synchronize_session=False
    )
    db.query(ClassSession).filter(ClassSession.teacher_id == user_id).update(
        {ClassSession.teacher_id: None},
        synchronize_session=False,
    )
    db.query(HomeworkResult).filter(HomeworkResult.returned_by_id == user_id).update(
        {HomeworkResult.returned_by_id: None},
        synchronize_session=False,
    )
    db.query(ClassEnrollment).filter(ClassEnrollment.student_id == user_id).delete(
        synchronize_session=False
    )


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    if normalize_role(current_user.role) not in ("admin", "mentor"):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    if current_user.id == user_id:
        return JSONResponse(
            status_code=403,
            content={
                "error": "SELF_DELETE_FORBIDDEN",
                "detail": "You cannot delete your own account",
            },
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    actor_role = normalize_role(current_user.role)
    target_role = normalize_role(user.role)
    if actor_role == "mentor" and target_role != "student":
        raise HTTPException(
            status_code=403,
            detail="Mentors can delete only students",
        )

    try:
        delete_user_dependents(db, user_id)
        db.delete(user)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return Response(status_code=204)
