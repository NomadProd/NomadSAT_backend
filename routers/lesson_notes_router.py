from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models import AcademicPlanItem, Class, ClassEnrollment, Session as ClassSession, User
from Methods.auth import get_db, require_roles
from schemas import (
    CreateSessionLessonNotesData,
    UpdateSessionAcademicPlanData,
    UpdateSessionLessonNotesData,
)

router = APIRouter(tags=["lesson-notes"])


def field_was_sent(data, field_name: str):
    fields_set = getattr(data, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(data, "__fields_set__", set())
    return field_name in fields_set


def ensure_class_access(
    current_user: User,
    class_obj: Class,
    db: Session,
    write: bool = False,
):
    if current_user.role in ["admin", "mentor"]:
        return

    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")
        return

    if write:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    enrollment = db.query(ClassEnrollment).filter(
        ClassEnrollment.class_id == class_obj.id,
        ClassEnrollment.student_id == current_user.id
    ).first()
    if not enrollment:
        raise HTTPException(status_code=403, detail="Not enough permissions")


def get_session_and_class(session_id: int, db: Session):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    return session_obj, class_obj


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


def serialize_session_lesson_notes(session_obj: ClassSession, db: Session):
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
        "academic_plan_item": serialize_academic_plan_item(plan_items[0]) if plan_items else None,
        "academic_plan_items": [serialize_academic_plan_item(plan_item) for plan_item in plan_items],
        "lesson_notes": session_obj.lesson_notes,
    }


def build_session_plan_payload(session_obj: ClassSession, message: str, db: Session):
    return {
        "message": message,
        **serialize_session_lesson_notes(session_obj, db),
    }


def get_plan_item_or_404(plan_item_id: int, db: Session):
    plan_item = db.query(AcademicPlanItem).filter(AcademicPlanItem.id == plan_item_id).first()
    if not plan_item:
        raise HTTPException(status_code=404, detail="Academic plan item not found")
    return plan_item


def get_subject_fallback(session_obj: ClassSession):
    fallback_subject = (session_obj.session_type or "verbal").strip().lower()
    return fallback_subject or "verbal"


def maybe_delete_orphaned_plan_item(plan_item: AcademicPlanItem, db: Session):
    linked_sessions = db.query(ClassSession).all()
    linked_count = sum(
        1
        for session_obj in linked_sessions
        if plan_item.id in normalize_plan_item_ids(session_obj.academic_plan_item_id)
    )
    if linked_count == 0:
        db.delete(plan_item)


def apply_plan_item_updates(
    session_obj: ClassSession,
    plan_item: AcademicPlanItem,
    data: UpdateSessionAcademicPlanData,
):
    if field_was_sent(data, "subject"):
        next_subject = (data.subject or "").strip().lower()
        plan_item.subject = next_subject or get_subject_fallback(session_obj)

    if field_was_sent(data, "general_topic"):
        plan_item.general_topic = data.general_topic

    if field_was_sent(data, "plan_text"):
        plan_item.plan_text = data.plan_text

    if field_was_sent(data, "lesson_notes"):
        session_obj.lesson_notes = data.lesson_notes

    if field_was_sent(data, "date") and data.date is not None:
        session_obj.date = data.date


@router.post("/sessions/{session_id}/lesson-notes")
def write_session_lesson_notes(
    session_id: int,
    data: CreateSessionLessonNotesData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"]))
):
    session_obj, class_obj = get_session_and_class(session_id, db)
    ensure_class_access(current_user, class_obj, db, write=True)

    requested_plan_item_ids = get_requested_plan_item_ids(data)
    if requested_plan_item_ids is not None:
        validate_academic_plan_items(requested_plan_item_ids, db)
        session_obj.academic_plan_item_id = requested_plan_item_ids or None

    session_obj.lesson_notes = data.lesson_notes

    db.commit()
    db.refresh(session_obj)

    return build_session_plan_payload(session_obj, "Lesson notes saved successfully", db)


@router.patch("/sessions/{session_id}/lesson-notes")
def update_session_lesson_notes(
    session_id: int,
    data: UpdateSessionLessonNotesData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"]))
):
    session_obj, class_obj = get_session_and_class(session_id, db)
    ensure_class_access(current_user, class_obj, db, write=True)

    requested_plan_item_ids = get_requested_plan_item_ids(data)
    if requested_plan_item_ids is not None:
        validate_academic_plan_items(requested_plan_item_ids, db)
        session_obj.academic_plan_item_id = requested_plan_item_ids or None

    if field_was_sent(data, "lesson_notes"):
        session_obj.lesson_notes = data.lesson_notes

    db.commit()
    db.refresh(session_obj)

    return build_session_plan_payload(session_obj, "Lesson notes updated successfully", db)


@router.delete("/sessions/{session_id}/lesson-notes")
def delete_session_lesson_notes(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"]))
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete lesson notes")

    session_obj, class_obj = get_session_and_class(session_id, db)
    ensure_class_access(current_user, class_obj, db, write=True)

    session_obj.lesson_notes = None

    db.commit()
    db.refresh(session_obj)

    return build_session_plan_payload(session_obj, "Lesson notes deleted successfully", db)


@router.get("/classes/{class_id}/lesson-notes")
def get_class_lesson_notes(
    class_id: int,
    db: Session = Depends(get_db)
):
    class_obj = db.query(Class).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    sessions = (
        db.query(ClassSession)
        .filter(ClassSession.class_id == class_id)
        .order_by(ClassSession.date, ClassSession.start_time, ClassSession.id)
        .all()
    )

    return [serialize_session_lesson_notes(session_obj, db) for session_obj in sessions]


@router.post("/sessions/{session_id}/academic-plan-items")
def create_session_academic_plan_item(
    session_id: int,
    data: UpdateSessionAcademicPlanData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    session_obj, class_obj = get_session_and_class(session_id, db)
    ensure_class_access(current_user, class_obj, db, write=True)

    plan_item = AcademicPlanItem(
        subject=get_subject_fallback(session_obj),
        general_topic=session_obj.topic,
        plan_text="",
    )
    db.add(plan_item)
    db.flush()

    current_plan_item_ids = normalize_plan_item_ids(session_obj.academic_plan_item_id)
    if plan_item.id not in current_plan_item_ids:
        current_plan_item_ids.append(plan_item.id)
        session_obj.academic_plan_item_id = current_plan_item_ids

    apply_plan_item_updates(session_obj, plan_item, data)

    db.commit()
    db.refresh(session_obj)

    return build_session_plan_payload(session_obj, "Academic plan added successfully", db)


@router.patch("/sessions/{session_id}/academic-plan-items/{plan_item_id}")
def update_session_academic_plan_item(
    session_id: int,
    plan_item_id: int,
    data: UpdateSessionAcademicPlanData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    session_obj, class_obj = get_session_and_class(session_id, db)
    ensure_class_access(current_user, class_obj, db, write=True)

    current_plan_item_ids = normalize_plan_item_ids(session_obj.academic_plan_item_id)
    if plan_item_id not in current_plan_item_ids:
        raise HTTPException(status_code=404, detail="Academic plan item is not assigned to this session")

    plan_item = get_plan_item_or_404(plan_item_id, db)
    apply_plan_item_updates(session_obj, plan_item, data)

    db.commit()
    db.refresh(session_obj)

    return build_session_plan_payload(session_obj, "Academic plan updated successfully", db)


@router.delete("/sessions/{session_id}/academic-plan-items/{plan_item_id}")
def delete_session_academic_plan_item(
    session_id: int,
    plan_item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete academic plans")

    session_obj, class_obj = get_session_and_class(session_id, db)
    ensure_class_access(current_user, class_obj, db, write=True)

    current_plan_item_ids = normalize_plan_item_ids(session_obj.academic_plan_item_id)
    if plan_item_id not in current_plan_item_ids:
        raise HTTPException(status_code=404, detail="Academic plan item is not assigned to this session")

    plan_item = get_plan_item_or_404(plan_item_id, db)
    session_obj.academic_plan_item_id = [current_id for current_id in current_plan_item_ids if current_id != plan_item_id] or None
    maybe_delete_orphaned_plan_item(plan_item, db)

    db.commit()
    db.refresh(session_obj)

    return build_session_plan_payload(session_obj, "Academic plan deleted successfully", db)


@router.patch("/sessions/{session_id}/academic-plan")
def update_session_academic_plan(
    session_id: int,
    data: UpdateSessionAcademicPlanData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    session_obj, class_obj = get_session_and_class(session_id, db)
    ensure_class_access(current_user, class_obj, db, write=True)

    current_plan_item_ids = normalize_plan_item_ids(session_obj.academic_plan_item_id)
    if current_plan_item_ids:
        return update_session_academic_plan_item(
            session_id=session_id,
            plan_item_id=current_plan_item_ids[0],
            data=UpdateSessionAcademicPlanData(
                subject=data.subject,
                general_topic=data.general_topic,
                plan_text=data.plan_text,
                lesson_notes=data.lesson_notes,
                date=data.date,
            ),
            db=db,
            current_user=current_user,
        )

    return create_session_academic_plan_item(
        session_id=session_id,
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete("/sessions/{session_id}/academic-plan")
def delete_session_academic_plan(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete academic plans")

    session_obj, class_obj = get_session_and_class(session_id, db)
    ensure_class_access(current_user, class_obj, db, write=True)

    current_plan_item_ids = normalize_plan_item_ids(session_obj.academic_plan_item_id)
    if not current_plan_item_ids:
        session_obj.lesson_notes = None
        db.commit()
        db.refresh(session_obj)
        return build_session_plan_payload(session_obj, "Academic plan deleted successfully", db)

    return delete_session_academic_plan_item(
        session_id=session_id,
        plan_item_id=current_plan_item_ids[0],
        db=db,
        current_user=current_user,
    )
