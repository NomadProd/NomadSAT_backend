from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Query, Session

from dependencies.auth import AuthUser, normalize_role
from models import (
    Assignment,
    Attendance,
    Class,
    ClassEnrollment,
    HomeworkResult,
    Session as ClassSession,
)


def classes_query(db: Session, user: AuthUser) -> Query:
    query = db.query(Class)
    role = normalize_role(user.role)

    if role in ("admin", "mentor"):
        return query
    if role == "teacher":
        return query.filter(
            or_(
                Class.verbal_teacher_id == user.id,
                Class.math_teacher_id == user.id,
            )
        )
    if role == "student":
        enrolled_class_ids = select(ClassEnrollment.class_id).where(
            ClassEnrollment.student_id == user.id
        )
        return query.filter(Class.id.in_(enrolled_class_ids))

    return query.filter(Class.id == -1)


def sessions_query(db: Session, user: AuthUser) -> Query:
    query = db.query(ClassSession)
    role = normalize_role(user.role)

    if role in ("admin", "mentor"):
        return query
    if role == "teacher":
        return query.filter(ClassSession.teacher_id == user.id)
    if role == "student":
        enrolled_class_ids = select(ClassEnrollment.class_id).where(
            ClassEnrollment.student_id == user.id
        )
        return query.filter(ClassSession.class_id.in_(enrolled_class_ids))

    return query.filter(ClassSession.id == -1)


def assignments_query(db: Session, user: AuthUser) -> Query:
    query = db.query(Assignment)
    role = normalize_role(user.role)

    if role in ("admin", "mentor", "teacher"):
        return query
    if role == "student":
        return query.filter(Assignment.student_id == user.id)

    return query.filter(Assignment.id == -1)


def homework_results_query(db: Session, user: AuthUser) -> Query:
    query = db.query(HomeworkResult)
    role = normalize_role(user.role)

    if role in ("admin", "mentor"):
        return query
    if role == "teacher":
        teacher_assignment_ids = (
            select(Assignment.id)
            .join(ClassSession, ClassSession.id == Assignment.session_id)
            .where(ClassSession.teacher_id == user.id)
        )
        return query.filter(HomeworkResult.assignment_id.in_(teacher_assignment_ids))
    if role == "student":
        student_assignment_ids = select(Assignment.id).where(
            Assignment.student_id == user.id
        )
        return query.filter(HomeworkResult.assignment_id.in_(student_assignment_ids))

    return query.filter(HomeworkResult.id == -1)


def attendance_query(db: Session, user: AuthUser) -> Query:
    query = db.query(Attendance)
    role = normalize_role(user.role)

    if role in ("admin", "mentor"):
        return query
    if role == "teacher":
        teacher_session_ids = select(ClassSession.id).where(
            ClassSession.teacher_id == user.id
        )
        return query.filter(Attendance.session_id.in_(teacher_session_ids))
    if role == "student":
        return query.filter(Attendance.student_id == user.id)

    return query.filter(Attendance.id == -1)
