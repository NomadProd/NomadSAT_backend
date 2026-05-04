from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models import Attendance, Session as ClassSession, User, Class, ClassEnrollment
from Methods.auth import get_db, require_roles
from schemas import AttendanceBulkData

router = APIRouter(prefix="/attendance", tags=["attendance"])


@router.post("/sessions/{session_id}")
def submit_or_update_attendance(
    session_id: int,
    data: AttendanceBulkData,
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

    for item in data.records:
        enrollment = db.query(ClassEnrollment).filter(
            ClassEnrollment.class_id == session_obj.class_id,
            ClassEnrollment.student_id == item.student_id
        ).first()

        if not enrollment:
            raise HTTPException(
                status_code=400,
                detail=f"Student {item.student_id} is not enrolled in this class"
            )

        attendance = db.query(Attendance).filter(
            Attendance.session_id == session_id,
            Attendance.student_id == item.student_id
        ).first()

        if attendance:
            attendance.status = item.status
        else:
            attendance = Attendance(
                session_id=session_id,
                student_id=item.student_id,
                status=item.status
            )
            db.add(attendance)

    db.commit()

    return {
        "message": "Attendance saved successfully",
        "session_id": session_id
    }


@router.get("/sessions/{session_id}")
def get_session_attendance(
    session_id: int,
    db: Session = Depends(get_db)
):
    session_obj = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    records = db.query(Attendance).filter(Attendance.session_id == session_id).all()

    return [
        {
            "attendance_id": record.id,
            "session_id": record.session_id,
            "student_id": record.student_id,
            "status": record.status
        }
        for record in records
    ]


@router.get("/students/{student_id}")
def get_student_attendance_history(
    student_id: int,
    db: Session = Depends(get_db)
):
    student = db.query(User).filter(User.id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    records = db.query(Attendance).filter(Attendance.student_id == student_id).all()

    return [
        {
            "attendance_id": record.id,
            "session_id": record.session_id,
            "student_id": record.student_id,
            "status": record.status
        }
        for record in records
    ]
