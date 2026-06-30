from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user
from dependencies.filters import assignments_query
from Methods.auth import get_db
from models import Assignment

router = APIRouter(tags=["assignments"])


def serialize_assignment(assignment: Assignment) -> dict:
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


@router.get("/assignments")
def list_assignments(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    assignments = assignments_query(db, current_user).order_by(Assignment.id.asc()).all()
    return [serialize_assignment(assignment) for assignment in assignments]


@router.get("/assignments/{assignment_id}")
def get_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    assignment = (
        assignments_query(db, current_user)
        .filter(Assignment.id == assignment_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return serialize_assignment(assignment)
