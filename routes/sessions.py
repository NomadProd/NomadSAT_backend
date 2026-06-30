from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user
from dependencies.filters import sessions_query
from Methods.auth import get_db
from models import AcademicPlanItem, Session as ClassSession

router = APIRouter(tags=["sessions"])


def normalize_plan_item_ids(raw_value) -> list[int]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [int(value) for value in raw_value if value is not None]
    if isinstance(raw_value, tuple):
        return [int(value) for value in raw_value if value is not None]
    return [int(raw_value)]


def get_session_plan_items(session_obj: ClassSession, db: Session) -> list[AcademicPlanItem]:
    plan_item_ids = normalize_plan_item_ids(session_obj.academic_plan_item_id)
    if not plan_item_ids:
        return []

    plan_items = db.query(AcademicPlanItem).filter(
        AcademicPlanItem.id.in_(plan_item_ids)
    ).all()
    plan_items_by_id = {plan_item.id: plan_item for plan_item in plan_items}
    return [
        plan_items_by_id[plan_item_id]
        for plan_item_id in plan_item_ids
        if plan_item_id in plan_items_by_id
    ]


def serialize_academic_plan_item(plan_item: AcademicPlanItem) -> dict:
    return {
        "id": plan_item.id,
        "subject": plan_item.subject,
        "general_topic": plan_item.general_topic,
        "plan_text": plan_item.plan_text,
    }


def serialize_session(session_obj: ClassSession, db: Session) -> dict:
    plan_items = get_session_plan_items(session_obj, db)
    plan_item_ids = normalize_plan_item_ids(session_obj.academic_plan_item_id)

    return {
        "session_id": session_obj.id,
        "class_id": session_obj.class_id,
        "teacher_id": session_obj.teacher_id,
        "date": session_obj.date,
        "start_time": session_obj.start_time,
        "end_time": session_obj.end_time,
        "session_type": session_obj.session_type,
        "topic": session_obj.topic,
        "academic_plan_item_id": plan_item_ids[0] if plan_item_ids else None,
        "academic_plan_item_ids": plan_item_ids,
        "academic_plan_items": [serialize_academic_plan_item(item) for item in plan_items],
        "lesson_notes": session_obj.lesson_notes,
    }


@router.get("/sessions")
def list_sessions(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    sessions = sessions_query(db, current_user).order_by(ClassSession.id.asc()).all()
    return [serialize_session(session_obj, db) for session_obj in sessions]


@router.get("/sessions/{session_id}")
def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    session_obj = (
        sessions_query(db, current_user)
        .filter(ClassSession.id == session_id)
        .first()
    )
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")
    return serialize_session(session_obj, db)
