from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user
from dependencies.filters import assignments_query, sessions_query
from models import Assignment, Session as ClassSession, Class, User, ClassEnrollment
from Methods.auth import get_db, require_roles
from schemas import CreateAssignmentData, UpdateAssignmentData, CopyAssignmentData

router = APIRouter(prefix="/assignments", tags=["assignments"])

MAX_HOMEWORK_SLOTS = 5


def ensure_class_staff_access(current_user: User, class_obj: Class) -> None:
    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")


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
) -> Assignment:
    title = source.title or f"Homework {slot_index}"
    if overwrite is not None:
        overwrite.slot_index = slot_index
        overwrite.title = title
        overwrite.instruction = source.instruction
        overwrite.task_link = source.task_link
        overwrite.due_date = source.due_date
        overwrite.due_time = source.due_time
        overwrite.photo_required = source.photo_required
        db.flush()
        return overwrite

    new_assignment = Assignment(
        session_id=session_id,
        student_id=student_id,
        slot_index=slot_index,
        title=title,
        instruction=source.instruction,
        task_link=source.task_link,
        due_date=source.due_date,
        due_time=source.due_time,
        photo_required=source.photo_required,
    )
    db.add(new_assignment)
    db.flush()
    return new_assignment


@router.post("/sessions/{session_id}")
def create_assignment_for_session(
    session_id: int,
    data: CreateAssignmentData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"]))
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
        "assignment_id": assignment.id,
        "session_id": assignment.session_id,
        "student_id": assignment.student_id,
        "slot_index": assignment.slot_index,
        "title": assignment.title,
        "instruction": assignment.instruction,
        "task_link": assignment.task_link,
        "due_date": assignment.due_date,
        "due_time": assignment.due_time,
        "photo_required": assignment.photo_required,
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

    return [
        {
            "assignment_id": a.id,
            "session_id": a.session_id,
            "student_id": a.student_id,
            "slot_index": a.slot_index,
            "title": a.title,
            "instruction": a.instruction,
            "task_link": a.task_link,
            "due_date": a.due_date,
            "due_time": a.due_time,
            "photo_required": a.photo_required,
        }
        for a in assignments
    ]



@router.post("/{assignment_id}/copy")
def copy_assignment(
    assignment_id: int,
    data: CopyAssignmentData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"])),
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
    if target_session_id != source.session_id:
        raise HTTPException(
            status_code=400,
            detail="Copying to a different session is not supported",
        )

    class_obj = db.query(Class).filter(Class.id == source_session.class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    ensure_class_staff_access(current_user, class_obj)

    if data.all_students or not data.target_student_ids:
        enrollments = (
            db.query(ClassEnrollment)
            .filter(ClassEnrollment.class_id == class_obj.id)
            .all()
        )
        target_student_ids = [enrollment.student_id for enrollment in enrollments]
    else:
        target_student_ids = list(dict.fromkeys(data.target_student_ids))

    target_student_ids = [
        student_id
        for student_id in target_student_ids
        if student_id != source.student_id
    ]

    if not target_student_ids:
        raise HTTPException(status_code=400, detail="No target students to copy to")

    created: list[dict] = []
    skipped: list[dict] = []

    for student_id in target_student_ids:
        student = db.query(User).filter(
            User.id == student_id,
            User.role == "student",
        ).first()
        if not student:
            skipped.append({"student_id": student_id, "reason": "STUDENT_NOT_FOUND"})
            continue

        enrollment = (
            db.query(ClassEnrollment)
            .filter(
                ClassEnrollment.class_id == class_obj.id,
                ClassEnrollment.student_id == student_id,
            )
            .first()
        )
        if not enrollment:
            skipped.append({"student_id": student_id, "reason": "NOT_ENROLLED"})
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


@router.patch("/{assignment_id}")
def update_assignment(
    assignment_id: int,
    data: UpdateAssignmentData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"]))
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
        "assignment_id": assignment.id,
        "session_id": assignment.session_id,
        "student_id": assignment.student_id,
        "slot_index": assignment.slot_index,
        "title": assignment.title,
        "instruction": assignment.instruction,
        "task_link": assignment.task_link,
        "due_date": assignment.due_date,
        "due_time": assignment.due_time,
        "photo_required": assignment.photo_required,
    }


@router.delete("/{assignment_id}")
def delete_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"]))
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
    elif current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admins and assigned teachers can delete assignments"
        )

    db.delete(assignment)
    db.commit()

    return {"message": "Assignment deleted successfully"}
