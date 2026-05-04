from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models import Assignment, Session as ClassSession, Class, User, ClassEnrollment
from Methods.auth import get_db, require_roles
from schemas import CreateAssignmentData, UpdateAssignmentData

router = APIRouter(prefix="/assignments", tags=["assignments"])


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

    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")

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
    db: Session = Depends(get_db)
):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    assignments = db.query(Assignment).filter(Assignment.session_id == session_id).all()

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


@router.get("/{assignment_id}")
def get_assignment_detail(
    assignment_id: int,
    db: Session = Depends(get_db)
):
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    return {
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
