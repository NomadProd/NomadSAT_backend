from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user, normalize_role
from dependencies.filters import classes_query, sessions_query
from Methods.auth import get_db
from models import (
    Assignment,
    Attendance,
    Class,
    ClassEnrollment,
    HomeworkResult,
    MockResult,
    Session as ClassSession,
    User,
)

router = APIRouter(tags=["classes"])


def serialize_class_summary(class_obj: Class, db: Session) -> dict:
    verbal_teacher = (
        db.query(User).filter(User.id == class_obj.verbal_teacher_id).first()
        if class_obj.verbal_teacher_id is not None
        else None
    )
    math_teacher = (
        db.query(User).filter(User.id == class_obj.math_teacher_id).first()
        if class_obj.math_teacher_id is not None
        else None
    )
    return {
        "class_id": class_obj.id,
        "class_name": class_obj.name,
        "verbal_teacher_id": class_obj.verbal_teacher_id,
        "math_teacher_id": class_obj.math_teacher_id,
        "verbal_teacher_name": verbal_teacher.name if verbal_teacher else None,
        "verbal_teacher_surname": verbal_teacher.surname if verbal_teacher else None,
        "math_teacher_name": math_teacher.name if math_teacher else None,
        "math_teacher_surname": math_teacher.surname if math_teacher else None,
    }


def serialize_session(session_obj: ClassSession) -> dict:
    return {
        "session_id": session_obj.id,
        "class_id": session_obj.class_id,
        "teacher_id": session_obj.teacher_id,
        "date": session_obj.date,
        "start_time": session_obj.start_time,
        "end_time": session_obj.end_time,
        "session_type": session_obj.session_type,
        "topic": session_obj.topic,
        "lesson_notes": session_obj.lesson_notes,
    }


@router.get("/classes")
@router.get("/classes/")
def list_classes(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    classes = classes_query(db, current_user).order_by(Class.id.asc()).all()
    return [serialize_class_summary(class_obj, db) for class_obj in classes]


@router.get("/classes/{class_id}")
def get_class(
    class_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    class_obj = classes_query(db, current_user).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    verbal_teacher = (
        db.query(User).filter(User.id == class_obj.verbal_teacher_id).first()
        if class_obj.verbal_teacher_id is not None
        else None
    )
    math_teacher = (
        db.query(User).filter(User.id == class_obj.math_teacher_id).first()
        if class_obj.math_teacher_id is not None
        else None
    )

    enrollments = db.query(ClassEnrollment).filter(
        ClassEnrollment.class_id == class_id
    ).all()
    student_ids = [enrollment.student_id for enrollment in enrollments]
    students = (
        db.query(User).filter(User.id.in_(student_ids)).all()
        if student_ids
        else []
    )
    sessions = (
        sessions_query(db, current_user)
        .filter(ClassSession.class_id == class_id)
        .order_by(ClassSession.id.asc())
        .all()
    )

    return {
        "class_id": class_obj.id,
        "class_name": class_obj.name,
        "verbal_teacher": {
            "user_id": verbal_teacher.id,
            "name": verbal_teacher.name,
            "surname": verbal_teacher.surname,
        } if verbal_teacher else None,
        "math_teacher": {
            "user_id": math_teacher.id,
            "name": math_teacher.name,
            "surname": math_teacher.surname,
        } if math_teacher else None,
        "students": [
            {
                "user_id": student.id,
                "name": student.name,
                "surname": student.surname,
            }
            for student in students
        ],
        "sessions": [serialize_session(session_obj) for session_obj in sessions],
    }


def delete_class_dependents(db: Session, class_id: int) -> None:
    session_ids_subq = select(ClassSession.id).where(ClassSession.class_id == class_id)
    assignment_ids_subq = select(Assignment.id).where(
        Assignment.session_id.in_(session_ids_subq)
    )

    db.execute(delete(MockResult).where(MockResult.assignment_id.in_(assignment_ids_subq)))
    db.execute(
        delete(HomeworkResult).where(HomeworkResult.assignment_id.in_(assignment_ids_subq))
    )
    db.execute(delete(Assignment).where(Assignment.session_id.in_(session_ids_subq)))
    db.execute(delete(Attendance).where(Attendance.session_id.in_(session_ids_subq)))
    db.execute(delete(ClassSession).where(ClassSession.class_id == class_id))
    db.execute(delete(ClassEnrollment).where(ClassEnrollment.class_id == class_id))


@router.delete("/classes/{class_id}", status_code=204)
def delete_class(
    class_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    if normalize_role(current_user.role) != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")

    class_obj = db.query(Class).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    try:
        delete_class_dependents(db, class_id)
        db.delete(class_obj)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return Response(status_code=204)


@router.delete("/classes/{class_id}/students/{student_id}", status_code=204)
def remove_student_from_class(
    class_id: int,
    student_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    if normalize_role(current_user.role) != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")

    enrollment = db.query(ClassEnrollment).filter(
        ClassEnrollment.class_id == class_id,
        ClassEnrollment.student_id == student_id,
    ).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    db.delete(enrollment)
    db.commit()
    return Response(status_code=204)
