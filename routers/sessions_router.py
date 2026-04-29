from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models import AcademicPlanItem, Class, Session as ClassSession, User
from Methods.auth import get_db, require_roles
from schemas import CreateSessionData, UpdateSessionData

router = APIRouter(tags=["sessions"])


def field_was_sent(data, field_name: str):
    fields_set = getattr(data, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(data, "__fields_set__", set())
    return field_name in fields_set


def normalize_plan_item_ids(raw_value) -> list[int]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [int(value) for value in raw_value if value is not None]
    if isinstance(raw_value, tuple):
        return [int(value) for value in raw_value if value is not None]
    return [int(raw_value)]


def get_requested_plan_item_ids(data) -> list[int] | None:
    if field_was_sent(data, "academic_plan_item_ids"):
        return list(dict.fromkeys(data.academic_plan_item_ids or []))

    if field_was_sent(data, "academic_plan_item_id"):
        if data.academic_plan_item_id is None:
            return []
        return [data.academic_plan_item_id]

    return None


def validate_academic_plan_items(academic_plan_item_ids: list[int], db: Session):
    if not academic_plan_item_ids:
        return

    plan_items = db.query(AcademicPlanItem).filter(
        AcademicPlanItem.id.in_(academic_plan_item_ids)
    ).all()
    found_ids = {plan_item.id for plan_item in plan_items}
    missing_ids = [plan_item_id for plan_item_id in academic_plan_item_ids if plan_item_id not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Academic plan items not found: {', '.join(str(item_id) for item_id in missing_ids)}",
        )


def get_session_plan_items(session_obj: ClassSession, db: Session) -> list[AcademicPlanItem]:
    plan_item_ids = normalize_plan_item_ids(session_obj.academic_plan_item_id)
    if not plan_item_ids:
        return []

    plan_items = db.query(AcademicPlanItem).filter(
        AcademicPlanItem.id.in_(plan_item_ids)
    ).all()
    plan_items_by_id = {plan_item.id: plan_item for plan_item in plan_items}
    return [plan_items_by_id[plan_item_id] for plan_item_id in plan_item_ids if plan_item_id in plan_items_by_id]


def serialize_academic_plan_item(plan_item: AcademicPlanItem):
    return {
        "id": plan_item.id,
        "subject": plan_item.subject,
        "general_topic": plan_item.general_topic,
        "plan_text": plan_item.plan_text,
    }


def serialize_session(session_obj: ClassSession, db: Session):
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
        "academic_plan_items": [serialize_academic_plan_item(plan_item) for plan_item in plan_items],
        "lesson_notes": session_obj.lesson_notes,
    }


@router.post("/classes/{class_id}/sessions")
def create_session(
    class_id: int,
    data: CreateSessionData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"]))
):
    class_obj = db.query(Class).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    teacher_id = data.teacher_id

    if teacher_id is None:
        if data.session_type == "verbal":
            teacher_id = class_obj.verbal_teacher_id
        elif data.session_type == "math":
            teacher_id = class_obj.math_teacher_id
        elif data.session_type == "mock":
            teacher_id = None

    if teacher_id is not None:
        teacher = db.query(User).filter(
            User.id == teacher_id,
            User.role == "teacher"
        ).first()
        if not teacher:
            raise HTTPException(status_code=404, detail="Teacher not found")

    requested_plan_item_ids = get_requested_plan_item_ids(data) or []
    validate_academic_plan_items(requested_plan_item_ids, db)

    new_session = ClassSession(
        class_id=class_id,
        teacher_id=teacher_id,
        date=data.date,
        start_time=data.start_time,
        end_time=data.end_time,
        session_type=data.session_type,
        topic=data.topic,
        academic_plan_item_id=requested_plan_item_ids or None,
        lesson_notes=data.lesson_notes
    )

    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    return {
        "message": "Session created successfully",
        **serialize_session(new_session, db),
    }


@router.get("/classes/{class_id}/sessions")
def get_class_sessions(
    class_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher", "student"]))
):
    class_obj = db.query(Class).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    sessions = db.query(ClassSession).filter(ClassSession.class_id == class_id).all()

    return [serialize_session(session_obj, db) for session_obj in sessions]


@router.get("/sessions/{session_id}")
def get_session_detail(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher", "student"]))
):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    return serialize_session(session_obj, db)


@router.patch("/sessions/{session_id}")
def update_session(
    session_id: int,
    data: UpdateSessionData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"]))
):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    if data.teacher_id is not None:
        teacher = db.query(User).filter(
            User.id == data.teacher_id,
            User.role == "teacher"
        ).first()
        if not teacher:
            raise HTTPException(status_code=404, detail="Teacher not found")
        session_obj.teacher_id = data.teacher_id

    if data.date is not None:
        session_obj.date = data.date

    if data.start_time is not None:
        session_obj.start_time = data.start_time

    if data.end_time is not None:
        session_obj.end_time = data.end_time

    if data.session_type is not None:
        session_obj.session_type = data.session_type

    if data.topic is not None:
        session_obj.topic = data.topic

    requested_plan_item_ids = get_requested_plan_item_ids(data)
    if requested_plan_item_ids is not None:
        validate_academic_plan_items(requested_plan_item_ids, db)
        session_obj.academic_plan_item_id = requested_plan_item_ids or None

    if field_was_sent(data, "lesson_notes"):
        session_obj.lesson_notes = data.lesson_notes

    db.commit()
    db.refresh(session_obj)

    return {
        "message": "Session updated successfully",
        **serialize_session(session_obj, db),
    }


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete sessions")

    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()

    db.delete(session_obj)
    db.commit()

    return {"message": "Session deleted successfully"}
