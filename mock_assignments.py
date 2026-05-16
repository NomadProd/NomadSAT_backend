from sqlalchemy.orm import Session

from models import Assignment, ClassEnrollment, Session as ClassSession


def ensure_mock_assignments_for_session(
    db: Session,
    session_obj: ClassSession,
    student_ids: list[int] | None = None,
) -> int:
    if session_obj.session_type != "mock":
        return 0

    target_student_ids = student_ids
    if target_student_ids is None:
        enrollments = db.query(ClassEnrollment).filter(
            ClassEnrollment.class_id == session_obj.class_id
        ).all()
        target_student_ids = [enrollment.student_id for enrollment in enrollments]

    created = 0
    for student_id in target_student_ids:
        existing = db.query(Assignment).filter(
            Assignment.session_id == session_obj.id,
            Assignment.student_id == student_id,
        ).first()
        if existing:
            continue

        db.add(
            Assignment(
                session_id=session_obj.id,
                student_id=student_id,
                title="Mock submission",
                instruction="Submit your mock result after the mock session starts.",
                due_date=session_obj.date,
                due_time=session_obj.end_time,
                photo_required=True,
            )
        )
        created += 1

    return created


def ensure_mock_assignments_for_class(db: Session, class_id: int) -> int:
    sessions = db.query(ClassSession).filter(
        ClassSession.class_id == class_id,
        ClassSession.session_type == "mock",
    ).all()

    created = 0
    for session_obj in sessions:
        created += ensure_mock_assignments_for_session(db, session_obj)
    return created
