from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user
from dependencies.filters import attendance_query
from Methods.auth import get_db
from models import Attendance

router = APIRouter(tags=["attendance"])


def serialize_attendance(record: Attendance) -> dict:
    return {
        "attendance_id": record.id,
        "session_id": record.session_id,
        "student_id": record.student_id,
        "status": record.status,
    }


@router.get("/attendance")
def list_attendance(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    records = attendance_query(db, current_user).order_by(Attendance.id.asc()).all()
    return [serialize_attendance(record) for record in records]
