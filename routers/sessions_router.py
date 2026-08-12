import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from dependencies.auth import AuthUser, get_current_user, require_admin_or_mentor
from dependencies.filters import classes_query, sessions_query
from models import AcademicPlanItem, Class, Session as ClassSession, User
from mock_assignments import ensure_mock_assignments_for_session
from Methods.auth import get_db, normalize_role, require_roles
from schemas import REVIEW_SUBJECTS, CreateSessionData, UpdateSessionData
from services.cloudinary_service import delete_raw_file, upload_mock_document
from services.homework_document import (
    mock_document_for_api,
    mock_document_upload_payload,
    read_homework_document,
    utc_now_iso,
    validate_homework_pdf,
    validation_error,
)

logger = logging.getLogger(__name__)

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
        "subject": session_obj.subject,
        "topic": session_obj.topic,
        "academic_plan_item_id": plan_item_ids[0] if plan_item_ids else None,
        "academic_plan_item_ids": plan_item_ids,
        "academic_plan_items": [serialize_academic_plan_item(plan_item) for plan_item in plan_items],
        "lesson_notes": session_obj.lesson_notes,
        "mock_document": mock_document_for_api(session_obj.mock_document),
    }


def is_admin_or_mentor_user(user: User) -> bool:
    return normalize_role(user.role) in ("admin", "mentor")


def resolve_review_subject(
    *,
    session_type: str | None,
    subject: str | None,
    existing_subject: str | None = None,
) -> str | None:
    normalized_type = (session_type or "").strip().lower()
    if normalized_type != "review":
        return None

    resolved = subject if subject is not None else existing_subject
    if resolved not in REVIEW_SUBJECTS:
        raise HTTPException(
            status_code=400,
            detail="subject must be 'verbal' or 'math' for review sessions",
        )
    return resolved


def default_teacher_id_for_session(
    class_obj: Class,
    session_type: str,
    subject: str | None,
) -> int | None:
    if session_type == "verbal":
        return class_obj.verbal_teacher_id
    if session_type == "math":
        return class_obj.math_teacher_id
    if session_type == "mock":
        return None
    if session_type == "review":
        if subject == "verbal":
            return class_obj.verbal_teacher_id
        if subject == "math":
            return class_obj.math_teacher_id
    return None


@router.post("/classes/{class_id}/sessions")
def create_session(
    class_id: int,
    data: CreateSessionData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "mentor", "teacher"]))
):
    class_obj = db.query(Class).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    if data.session_type == "review" and not is_admin_or_mentor_user(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    subject = resolve_review_subject(
        session_type=data.session_type,
        subject=data.subject,
    )

    teacher_id = data.teacher_id
    if teacher_id is None:
        teacher_id = default_teacher_id_for_session(
            class_obj,
            data.session_type,
            subject,
        )

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
        subject=subject,
        topic=data.topic,
        academic_plan_item_id=requested_plan_item_ids or None,
        lesson_notes=data.lesson_notes
    )

    db.add(new_session)
    db.flush()
    ensure_mock_assignments_for_session(db, new_session)
    db.commit()
    db.refresh(new_session)

    return {
        "message": "Session created successfully",
        **serialize_session(new_session, db),
    }


@router.get("/classes/{class_id}/sessions")
def get_class_sessions(
    class_id: int,
    session_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    class_obj = classes_query(db, current_user).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    query = (
        sessions_query(db, current_user)
        .filter(ClassSession.class_id == class_id)
    )
    if session_type:
        query = query.filter(ClassSession.session_type == session_type.strip().lower())

    sessions = query.all()

    return [serialize_session(session_obj, db) for session_obj in sessions]



@router.patch("/sessions/{session_id}")
def update_session(
    session_id: int,
    data: UpdateSessionData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "mentor", "teacher"]))
):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    current_type = (session_obj.session_type or "").strip().lower()
    next_type = data.session_type if data.session_type is not None else current_type
    touches_review = current_type == "review" or next_type == "review"
    if touches_review and not is_admin_or_mentor_user(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")

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

    if field_was_sent(data, "subject") or data.session_type is not None:
        session_obj.subject = resolve_review_subject(
            session_type=next_type,
            subject=data.subject if field_was_sent(data, "subject") else session_obj.subject,
            existing_subject=session_obj.subject,
        )

    if data.topic is not None:
        session_obj.topic = data.topic

    requested_plan_item_ids = get_requested_plan_item_ids(data)
    if requested_plan_item_ids is not None:
        validate_academic_plan_items(requested_plan_item_ids, db)
        session_obj.academic_plan_item_id = requested_plan_item_ids or None

    if field_was_sent(data, "lesson_notes"):
        session_obj.lesson_notes = data.lesson_notes

    ensure_mock_assignments_for_session(db, session_obj)
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
    current_user: User = Depends(require_roles(["admin", "mentor"]))
):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    db.delete(session_obj)
    db.commit()

    return {"message": "Session deleted successfully"}


@router.post("/sessions/{session_id}/mock-document")
async def upload_session_mock_document(
    session_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    session_type = (session_obj.session_type or "").strip().lower()
    if session_type != "mock":
        return validation_error(
            {
                "error": "INVALID_SESSION_TYPE",
                "detail": "A mock test PDF can only be attached to a mock session",
            }
        )

    filename = file.filename or "upload.pdf"
    file_bytes = await file.read()
    validation = validate_homework_pdf(
        filename=filename,
        content_type=file.content_type,
        file_bytes=file_bytes,
    )
    if validation is not None:
        return validation

    old_document = read_homework_document(session_obj.mock_document)
    uploaded = upload_mock_document(
        file_bytes,
        session_id=session_obj.id,
        filename=filename,
    )
    new_document = {
        "url": uploaded["url"],
        "secure_url": uploaded["secure_url"],
        "public_id": uploaded["public_id"],
        "filename": filename,
        "content_type": "application/pdf",
        "size_bytes": int(uploaded["size_bytes"]),
        "uploaded_at": utc_now_iso(),
        "uploaded_by_id": current_user.id,
    }

    try:
        session_obj.mock_document = new_document
        flag_modified(session_obj, "mock_document")
        db.commit()
        db.refresh(session_obj)
    except Exception:
        db.rollback()
        session_obj.mock_document = old_document
        delete_raw_file(str(uploaded["public_id"]))
        logger.error(
            "Failed to save mock_document for session_id=%s",
            session_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to save mock test document",
        )

    old_public_id = (old_document or {}).get("public_id")
    if old_public_id and old_public_id != uploaded["public_id"]:
        delete_raw_file(str(old_public_id))

    return {
        "session_id": session_obj.id,
        "mock_document": mock_document_upload_payload(session_obj.mock_document),
    }


@router.delete("/sessions/{session_id}/mock-document", status_code=204)
def delete_session_mock_document(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    document = read_homework_document(session_obj.mock_document)
    if document is None:
        raise HTTPException(status_code=404, detail="Mock test PDF not found")

    public_id = document.get("public_id")
    if public_id:
        delete_raw_file(str(public_id))

    session_obj.mock_document = None
    flag_modified(session_obj, "mock_document")
    db.commit()
    return Response(status_code=204)
