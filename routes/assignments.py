from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user
from dependencies.filters import assignments_query
from Methods.auth import get_db
from models import Assignment
from services.homework_document import serialize_assignment

router = APIRouter(tags=["assignments"])


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
