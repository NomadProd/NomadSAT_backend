import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from dependencies.auth import (
    AuthUser,
    get_current_user,
    is_admin_or_mentor,
    require_admin_or_mentor,
)
from dependencies.filters import assignments_query, sessions_query
from models import Assignment, Session as ClassSession, Class, User, ClassEnrollment
from Methods.auth import get_db, require_roles
from schemas import CreateAssignmentData, UpdateAssignmentData, CopyAssignmentData
from services.cloudinary_service import delete_raw_file, upload_homework_document
from services.homework_document import (
    homework_document_upload_payload,
    read_homework_document,
    serialize_assignment,
    utc_now_iso,
    validate_homework_pdf,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assignments", tags=["assignments"])

MAX_HOMEWORK_SLOTS = 5


def ensure_class_staff_access(current_user: User, class_obj: Class) -> None:
    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")


def ensure_teacher_copy_session_access(
    current_user: User,
    source_session: ClassSession,
    target_session: ClassSession,
) -> None:
    if current_user.role != "teacher":
        return
    if source_session.teacher_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You can only copy your own assignments",
        )
    if target_session.teacher_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You can only copy assignments into your own sessions",
        )


def ensure_teacher_bulk_targets_in_class(
    current_user: User,
    class_id: int,
    target_student_ids: list[int],
    db: Session,
) -> None:
    if current_user.role != "teacher":
        return
    for student_id in target_student_ids:
        student = (
            db.query(User)
            .filter(User.id == student_id, User.role == "student")
            .first()
        )
        if not student:
            raise HTTPException(
                status_code=403,
                detail="You can only copy assignments to students in your classes",
            )
        enrollment = (
            db.query(ClassEnrollment)
            .filter(
                ClassEnrollment.class_id == class_id,
                ClassEnrollment.student_id == student_id,
            )
            .first()
        )
        if not enrollment:
            raise HTTPException(
                status_code=403,
                detail="You can only copy assignments to students in your classes",
            )


def resolve_same_session_copy_targets(
    data: CopyAssignmentData,
    class_id: int,
    db: Session,
    *,
    source_student_id: int,
) -> list[int]:
    if data.all_students:
        enrollments = (
            db.query(ClassEnrollment)
            .filter(ClassEnrollment.class_id == class_id)
            .all()
        )
        target_student_ids = [enrollment.student_id for enrollment in enrollments]
    elif data.target_student_ids is not None:
        if not data.target_student_ids:
            raise HTTPException(
                status_code=422,
                detail="Provide at least one target student or set all_students=true",
            )
        target_student_ids = list(dict.fromkeys(data.target_student_ids))
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide target_student_ids or set all_students=true",
        )

    target_student_ids = [
        student_id
        for student_id in target_student_ids
        if student_id != source_student_id
    ]
    if not target_student_ids:
        raise HTTPException(status_code=400, detail="No target students to copy to")
    return target_student_ids


def resolve_cross_session_copy_targets(
    data: CopyAssignmentData,
    class_id: int,
    db: Session,
) -> list[int]:
    if data.student_id is not None:
        target_student_ids = [data.student_id]
    elif data.target_student_ids is not None:
        if not data.target_student_ids:
            raise HTTPException(
                status_code=422,
                detail="Provide at least one target student or set all_students=true",
            )
        target_student_ids = list(dict.fromkeys(data.target_student_ids))
    elif data.all_students:
        enrollments = (
            db.query(ClassEnrollment)
            .filter(ClassEnrollment.class_id == class_id)
            .all()
        )
        target_student_ids = [enrollment.student_id for enrollment in enrollments]
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide student_id, target_student_ids, or all_students for cross-session copy",
        )

    if not target_student_ids:
        raise HTTPException(status_code=400, detail="No target students to copy to")
    return target_student_ids


def target_student_skip_reason(
    current_user: User,
    class_id: int,
    student_id: int,
    db: Session,
) -> str | None:
    if current_user.role == "teacher":
        return None
    student = (
        db.query(User)
        .filter(User.id == student_id, User.role == "student")
        .first()
    )
    if not student:
        return "STUDENT_NOT_FOUND"
    enrollment = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.student_id == student_id,
        )
        .first()
    )
    if not enrollment:
        return "NOT_ENROLLED"
    return None


def used_homework_slots(db: Session, session_id: int, student_id: int) -> set[int]:
    rows = (
        db.query(Assignment.slot_index)
        .filter(
            Assignment.session_id == session_id,
            Assignment.student_id == student_id,
            Assignment.slot_index.isnot(None),
        )
        .all()
    )
    return {
        int(row[0])
        for row in rows
        if row[0] is not None and 1 <= int(row[0]) <= MAX_HOMEWORK_SLOTS
    }


def next_free_homework_slot(db: Session, session_id: int, student_id: int) -> int | None:
    used = used_homework_slots(db, session_id, student_id)
    for slot in range(1, MAX_HOMEWORK_SLOTS + 1):
        if slot not in used:
            return slot
    return None


def slot_is_free(db: Session, session_id: int, student_id: int, slot_index: int) -> bool:
    existing = (
        db.query(Assignment.id)
        .filter(
            Assignment.session_id == session_id,
            Assignment.student_id == student_id,
            Assignment.slot_index == slot_index,
        )
        .first()
    )
    return existing is None


def assignment_is_empty(assignment: Assignment) -> bool:
    if (assignment.instruction or "").strip():
        return False
    if (assignment.task_link or "").strip():
        return False
    if assignment.due_date is not None:
        return False
    if read_homework_document(assignment.homework_document) is not None:
        return False
    return True


def get_assignment_at_slot(
    db: Session, session_id: int, student_id: int, slot_index: int
) -> Assignment | None:
    return (
        db.query(Assignment)
        .filter(
            Assignment.session_id == session_id,
            Assignment.student_id == student_id,
            Assignment.slot_index == slot_index,
        )
        .first()
    )


def find_copy_target_slot(
    db: Session, session_id: int, student_id: int
) -> tuple[int | None, Assignment | None]:
    """First slot with no assignment or an empty placeholder; reuse empty rows."""
    for slot in range(1, MAX_HOMEWORK_SLOTS + 1):
        existing = get_assignment_at_slot(db, session_id, student_id, slot)
        if existing is None:
            return slot, None
        if assignment_is_empty(existing):
            return slot, existing

    unslotted = (
        db.query(Assignment)
        .filter(
            Assignment.session_id == session_id,
            Assignment.student_id == student_id,
            Assignment.slot_index.is_(None),
        )
        .all()
    )
    for existing in unslotted:
        if not assignment_is_empty(existing):
            continue
        for slot in range(1, MAX_HOMEWORK_SLOTS + 1):
            if get_assignment_at_slot(db, session_id, student_id, slot) is None:
                existing.slot_index = slot
                return slot, existing

    return None, None


def apply_copy_to_target(
    db: Session,
    source: Assignment,
    session_id: int,
    student_id: int,
    slot_index: int,
    overwrite: Assignment | None,
    due_date=None,
    due_time=None,
) -> Assignment:
    title = source.title or f"Homework {slot_index}"
    next_due_date = source.due_date if due_date is None else due_date
    next_due_time = source.due_time if due_time is None else due_time
    if overwrite is not None:
        overwrite.slot_index = slot_index
        overwrite.title = title
        overwrite.instruction = source.instruction
        overwrite.task_link = source.task_link
        overwrite.due_date = next_due_date
        overwrite.due_time = next_due_time
        overwrite.photo_required = source.photo_required
        overwrite.homework_document = read_homework_document(source.homework_document)
        db.flush()
        return overwrite

    new_assignment = Assignment(
        session_id=session_id,
        student_id=student_id,
        slot_index=slot_index,
        title=title,
        instruction=source.instruction,
        task_link=source.task_link,
        due_date=next_due_date,
        due_time=next_due_time,
        photo_required=source.photo_required,
        homework_document=read_homework_document(source.homework_document),
    )
    db.add(new_assignment)
    db.flush()
    return new_assignment


@router.post("/sessions/{session_id}")
def create_assignment_for_session(
    session_id: int,
    data: CreateAssignmentData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "mentor", "teacher"]))
):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    ensure_class_staff_access(current_user, class_obj)

    student = db.query(User).filter(
        User.id == data.student_id,
        User.role == "student"
    ).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    enrollment = db.query(ClassEnrollment).filter(
        ClassEnrollment.class_id == session_obj.class_id,
        ClassEnrollment.student_id == data.student_id
    ).first()
    if not enrollment:
        raise HTTPException(status_code=400, detail="Student is not enrolled in this class")

    if data.slot_index is not None:
        existing = db.query(Assignment).filter(
            Assignment.session_id == session_id,
            Assignment.student_id == data.student_id,
            Assignment.slot_index == data.slot_index
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Assignment slot already exists for this student in this session"
            )

    assignment = Assignment(
        session_id=session_id,
        student_id=data.student_id,
        slot_index=data.slot_index,
        title=data.title,
        instruction=data.instruction,
        task_link=data.task_link,
        due_date=data.due_date,
        due_time=data.due_time,
        photo_required=data.photo_required,
    )

    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    return {
        "message": "Assignment created successfully",
        **serialize_assignment(assignment),
    }


@router.get("/sessions/{session_id}")
def get_assignments_by_session(
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

    assignments = (
        assignments_query(db, current_user)
        .filter(Assignment.session_id == session_id)
        .all()
    )

    return [serialize_assignment(a) for a in assignments]



@router.post("/{assignment_id}/copy")
def copy_assignment(
    assignment_id: int,
    data: CopyAssignmentData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "mentor", "teacher"])),
):
    source = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Assignment not found")

    source_session = (
        db.query(ClassSession).filter(ClassSession.id == source.session_id).first()
    )
    if not source_session:
        raise HTTPException(status_code=404, detail="Session not found")

    target_session_id = data.session_id or source.session_id
    if target_session_id == source.session_id:
        target_session = source_session
    else:
        target_session = (
            db.query(ClassSession).filter(ClassSession.id == target_session_id).first()
        )
        if not target_session:
            raise HTTPException(status_code=404, detail="Target session not found")

    source_class = db.query(Class).filter(Class.id == source_session.class_id).first()
    if not source_class:
        raise HTTPException(status_code=404, detail="Source class not found")

    if target_session.class_id == source_session.class_id:
        target_class = source_class
    else:
        target_class = db.query(Class).filter(Class.id == target_session.class_id).first()
        if not target_class:
            raise HTTPException(status_code=404, detail="Target class not found")

    if target_class.archived:
        raise HTTPException(
            status_code=400,
            detail="Cannot copy assignments into an archived class",
        )

    ensure_teacher_copy_session_access(current_user, source_session, target_session)

    if target_session_id == source.session_id:
        class_obj = target_class
        target_student_ids = resolve_same_session_copy_targets(
            data,
            class_obj.id,
            db,
            source_student_id=source.student_id,
        )
        ensure_teacher_bulk_targets_in_class(
            current_user,
            class_obj.id,
            target_student_ids,
            db,
        )

        created: list[dict] = []
        skipped: list[dict] = []

        for student_id in target_student_ids:
            skip_reason = target_student_skip_reason(
                current_user,
                class_obj.id,
                student_id,
                db,
            )
            if skip_reason is not None:
                skipped.append({"student_id": student_id, "reason": skip_reason})
                continue

            overwrite: Assignment | None = None
            if data.target_slot_index is not None:
                slot_index = data.target_slot_index
                if not (1 <= slot_index <= MAX_HOMEWORK_SLOTS):
                    skipped.append(
                        {"student_id": student_id, "reason": "INVALID_SLOT"}
                    )
                    continue
                existing = get_assignment_at_slot(
                    db, target_session_id, student_id, slot_index
                )
                if existing is not None and not assignment_is_empty(existing):
                    skipped.append({"student_id": student_id, "reason": "SLOT_OCCUPIED"})
                    continue
                overwrite = existing
            else:
                slot_index, overwrite = find_copy_target_slot(
                    db, target_session_id, student_id
                )
                if slot_index is None:
                    skipped.append({"student_id": student_id, "reason": "NO_FREE_SLOT"})
                    continue

            target_assignment = apply_copy_to_target(
                db,
                source,
                target_session_id,
                student_id,
                slot_index,
                overwrite,
            )
            created.append(
                {
                    "student_id": student_id,
                    "assignment_id": target_assignment.id,
                    "slot_index": slot_index,
                    "updated": overwrite is not None,
                }
            )

        db.commit()

        return {
            "message": f"Copied to {len(created)} student(s), skipped {len(skipped)}",
            "source_assignment_id": source.id,
            "created": created,
            "skipped": skipped,
        }

    if data.due_date is None or data.due_time is None:
        raise HTTPException(
            status_code=422,
            detail="due_date and due_time are required when copying to another session",
        )

    target_student_ids = resolve_cross_session_copy_targets(
        data,
        target_class.id,
        db,
    )
    ensure_teacher_bulk_targets_in_class(
        current_user,
        target_class.id,
        target_student_ids,
        db,
    )

    created: list[dict] = []
    skipped: list[dict] = []
    for student_id in target_student_ids:
        skip_reason = target_student_skip_reason(
            current_user,
            target_class.id,
            student_id,
            db,
        )
        if skip_reason is not None:
            skipped.append({"student_id": student_id, "reason": skip_reason})
            continue

        overwrite: Assignment | None = None
        if data.target_slot_index is not None:
            slot_index = data.target_slot_index
            if not (1 <= slot_index <= MAX_HOMEWORK_SLOTS):
                skipped.append({"student_id": student_id, "reason": "INVALID_SLOT"})
                continue
            existing = get_assignment_at_slot(
                db, target_session_id, student_id, slot_index
            )
            if existing is not None and not assignment_is_empty(existing):
                skipped.append({"student_id": student_id, "reason": "SLOT_OCCUPIED"})
                continue
            overwrite = existing
        else:
            slot_index, overwrite = find_copy_target_slot(
                db, target_session_id, student_id
            )
            if slot_index is None:
                skipped.append({"student_id": student_id, "reason": "NO_FREE_SLOT"})
                continue

        target_assignment = apply_copy_to_target(
            db,
            source,
            target_session_id,
            student_id,
            slot_index,
            overwrite,
            due_date=data.due_date,
            due_time=data.due_time,
        )
        created.append(
            {
                "student_id": student_id,
                "assignment_id": target_assignment.id,
                "slot_index": slot_index,
                "updated": overwrite is not None,
            }
        )

    db.commit()
    return {
        "message": f"Copied to {len(created)} student(s), skipped {len(skipped)}",
        "source_assignment_id": source.id,
        "created": created,
        "skipped": skipped,
    }


@router.patch("/{assignment_id}")
def update_assignment(
    assignment_id: int,
    data: UpdateAssignmentData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "mentor", "teacher"]))
):
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    session_obj = db.query(ClassSession).filter(ClassSession.id == assignment.session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    new_student_id = assignment.student_id if data.student_id is None else data.student_id
    new_slot_index = assignment.slot_index if data.slot_index is None else data.slot_index

    if data.student_id is not None:
        student = db.query(User).filter(
            User.id == data.student_id,
            User.role == "student"
        ).first()
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        enrollment = db.query(ClassEnrollment).filter(
            ClassEnrollment.class_id == session_obj.class_id,
            ClassEnrollment.student_id == data.student_id
        ).first()
        if not enrollment:
            raise HTTPException(status_code=400, detail="Student is not enrolled in this class")

    if new_slot_index is not None:
        existing = db.query(Assignment).filter(
            Assignment.session_id == assignment.session_id,
            Assignment.student_id == new_student_id,
            Assignment.slot_index == new_slot_index,
            Assignment.id != assignment.id
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Assignment slot already exists for this student in this session"
            )

    if data.student_id is not None:
        assignment.student_id = data.student_id
    if data.slot_index is not None:
        assignment.slot_index = data.slot_index
    if data.title is not None:
        assignment.title = data.title
    if data.instruction is not None:
        assignment.instruction = data.instruction
    if data.task_link is not None:
        assignment.task_link = data.task_link
    if data.due_date is not None:
        assignment.due_date = data.due_date
    if data.due_time is not None:
        assignment.due_time = data.due_time
    if data.photo_required is not None:
        assignment.photo_required = data.photo_required

    db.commit()
    db.refresh(assignment)

    return {
        "message": "Assignment updated successfully",
        **serialize_assignment(assignment),
    }


@router.post("/{assignment_id}/homework-document")
async def upload_assignment_homework_document(
    assignment_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    filename = file.filename or "upload.pdf"
    file_bytes = await file.read()
    validation = validate_homework_pdf(
        filename=filename,
        content_type=file.content_type,
        file_bytes=file_bytes,
    )
    if validation is not None:
        return validation

    old_document = read_homework_document(assignment.homework_document)
    uploaded = upload_homework_document(
        file_bytes,
        assignment_id=assignment.id,
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
    }

    try:
        assignment.homework_document = new_document
        flag_modified(assignment, "homework_document")
        db.commit()
        db.refresh(assignment)
    except Exception:
        db.rollback()
        assignment.homework_document = old_document
        delete_raw_file(str(uploaded["public_id"]))
        logger.error(
            "Failed to save homework_document for assignment_id=%s",
            assignment_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to save homework document",
        )

    old_public_id = (old_document or {}).get("public_id")
    if old_public_id and old_public_id != uploaded["public_id"]:
        delete_raw_file(str(old_public_id))

    return {
        "assignment_id": assignment.id,
        "homework_document": homework_document_upload_payload(
            assignment.homework_document
        ),
    }


@router.delete("/{assignment_id}/homework-document", status_code=204)
def delete_assignment_homework_document(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    document = read_homework_document(assignment.homework_document)
    if document is None:
        raise HTTPException(status_code=404, detail="Homework PDF not found")

    public_id = document.get("public_id")
    if public_id:
        delete_raw_file(str(public_id))

    assignment.homework_document = None
    flag_modified(assignment, "homework_document")
    db.commit()
    return Response(status_code=204)


@router.delete("/{assignment_id}")
def delete_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "mentor", "teacher"]))
):
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    session_obj = db.query(ClassSession).filter(ClassSession.id == assignment.session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    if current_user.role == "teacher":
        teacher_ids = {class_obj.verbal_teacher_id, class_obj.math_teacher_id}
        if current_user.id not in teacher_ids:
            raise HTTPException(
                status_code=403,
                detail="Only assigned teachers can delete this assignment"
            )
    elif not is_admin_or_mentor(current_user.role):
        raise HTTPException(
            status_code=403,
            detail="Only admins and assigned teachers can delete assignments"
        )

    db.delete(assignment)
    db.commit()

    return {"message": "Assignment deleted successfully"}
