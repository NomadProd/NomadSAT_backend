from datetime import time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user, require_admin, require_staff
from dependencies.filters import classes_query, sessions_query, homework_results_query
from models import AcademicPlanItem, Class, User, ClassEnrollment, Assignment, Attendance, HomeworkResult, MockResult, Session as ClassSession
from mock_assignments import ensure_mock_assignments_for_class
from Methods.auth import get_db, require_roles
from routes.mock_results import serialize_mock_result_list_item
from schemas import CreateClassData, UpdateClassData, EnrollmentData
from services.attachments import read_submission_history

router = APIRouter(prefix="/classes", tags=["classes"])

LESSON_START_TIME = time(18, 30)
LESSON_END_TIME = time(20, 0)
MOCK_START_TIME = time(9, 0)
MOCK_END_TIME = time(16, 30)
SCHEDULE_WEEKS = {
    "intensive": 4,
    "standard": 8,
    "standart": 8,
}

SCHEDULE_TEMPLATES = {
    "intensive": ["verbal", "math", "verbal", "math", "verbal", "mock", "mock"],
    "standard": ["verbal", None, "math", None, "verbal", "mock", None],
    "standart": ["verbal", None, "math", None, "verbal", "mock", None],
}


def ensure_class_access(current_user: User, class_obj: Class, db: Session):
    if current_user.role in ["admin", "mentor"]:
        return

    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")
        return

    enrollment = db.query(ClassEnrollment).filter(
        ClassEnrollment.class_id == class_obj.id,
        ClassEnrollment.student_id == current_user.id
    ).first()
    if not enrollment:
        raise HTTPException(status_code=403, detail="Not enough permissions")

def normalize_session_plan_item_ids(raw_value) -> list[int]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [int(value) for value in raw_value if value is not None]
    if isinstance(raw_value, tuple):
        return [int(value) for value in raw_value if value is not None]
    return [int(raw_value)]


def get_session_plan_items(session_obj: ClassSession, db: Session) -> list[AcademicPlanItem]:
    plan_item_ids = normalize_session_plan_item_ids(session_obj.academic_plan_item_id)
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


def calc_accuracy(correct, incorrect):
    if correct is None or incorrect is None:
        return None
    total = correct + incorrect
    if total == 0:
        return None
    return round(correct * 100 / total, 2)


def serialize_class_homework_result_row(
    result: HomeworkResult,
    student_id: int,
    *,
    history_id: int | None = None,
    is_historical: bool = False,
    history_entry: dict | None = None,
):
    if is_historical and history_entry is not None:
        return {
            "result_id": result.id,
            "history_id": history_entry.get("history_id"),
            "is_historical": True,
            "assignment_id": result.assignment_id,
            "student_id": student_id,
            "submitted": True,
            "submitted_at": history_entry.get("submitted_at"),
            "photo_link": result.photo_link,
            "correct_total": history_entry.get("correct_total"),
            "incorrect_total": history_entry.get("incorrect_total"),
            "analysis": history_entry.get("analysis"),
            "accuracy": calc_accuracy(
                history_entry.get("correct_total"),
                history_entry.get("incorrect_total"),
            ),
        }

    return {
        "result_id": result.id,
        "history_id": None,
        "is_historical": False,
        "assignment_id": result.assignment_id,
        "student_id": student_id,
        "submitted": result.submitted,
        "submitted_at": result.submitted_at,
        "photo_link": result.photo_link,
        "correct_total": result.correct_total,
        "incorrect_total": result.incorrect_total,
        "analysis": result.analysis,
        "accuracy": calc_accuracy(result.correct_total, result.incorrect_total),
    }


def expand_class_homework_result_rows(
    result: HomeworkResult,
    student_id: int,
) -> list[dict]:
    history = read_submission_history(result)
    rows = [
        serialize_class_homework_result_row(
            result,
            student_id,
            history_id=int(entry.get("history_id", 0)),
            is_historical=True,
            history_entry=entry,
        )
        for entry in history
    ]
    if result.submitted or not history:
        rows.append(serialize_class_homework_result_row(result, student_id))
    return rows


def serialize_session(session_obj: ClassSession, db: Session):
    plan_items = get_session_plan_items(session_obj, db)
    plan_item_ids = normalize_session_plan_item_ids(session_obj.academic_plan_item_id)

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
        "academic_plan_items": [serialize_academic_plan_item(plan_item) for plan_item in plan_items],
        "lesson_notes": session_obj.lesson_notes,
    }


def build_template_sessions(data: CreateClassData, class_id: int, db: Session):
    if data.schedule_template is None and data.start_date is None:
        return []

    if data.schedule_template is None or data.start_date is None:
        raise HTTPException(
            status_code=400,
            detail="schedule_template and start_date must be provided together"
        )

    template_key = data.schedule_template.lower().strip()
    template = SCHEDULE_TEMPLATES.get(template_key)
    if template is None:
        raise HTTPException(
            status_code=400,
            detail="schedule_template must be 'intensive' or 'standard'"
        )

    sessions = []

    schedule_weeks = SCHEDULE_WEEKS.get(template_key, 4)

    for day_offset in range(schedule_weeks * 7):
        session_type = template[day_offset % 7]
        if session_type is None:
            continue

        session_date = data.start_date + timedelta(days=day_offset)
        teacher_id = None
        academic_plan_item_ids = None
        start_time = MOCK_START_TIME
        end_time = MOCK_END_TIME
        topic = "Mock test and review"

        if session_type == "verbal":
            teacher_id = data.verbal_teacher_id
            start_time = LESSON_START_TIME
            end_time = LESSON_END_TIME
            topic = "Verbal lesson"
        elif session_type == "math":
            teacher_id = data.math_teacher_id
            start_time = LESSON_START_TIME
            end_time = LESSON_END_TIME
            topic = "Math lesson"

        sessions.append(ClassSession(
            class_id=class_id,
            teacher_id=teacher_id,
            date=session_date,
            start_time=start_time,
            end_time=end_time,
            session_type=session_type,
            topic=topic,
            academic_plan_item_id=academic_plan_item_ids,
        ))

    return sessions


@router.post("/")
def create_class(
    data: CreateClassData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    verbal_teacher = db.query(User).filter(
        User.id == data.verbal_teacher_id,
        User.role == "teacher"
    ).first()
    if not verbal_teacher:
        raise HTTPException(status_code=404, detail="Verbal teacher not found")

    math_teacher = db.query(User).filter(
        User.id == data.math_teacher_id,
        User.role == "teacher"
    ).first()
    if not math_teacher:
        raise HTTPException(status_code=404, detail="Math teacher not found")

    new_class = Class(
        name=data.name,
        verbal_teacher_id=data.verbal_teacher_id,
        math_teacher_id=data.math_teacher_id
    )

    db.add(new_class)
    db.flush()

    sessions = build_template_sessions(data, new_class.id, db)
    db.add_all(sessions)
    db.commit()
    db.refresh(new_class)

    return {
        "message": "Class created successfully",
        "class_id": new_class.id,
        "name": new_class.name,
        "verbal_teacher_id": new_class.verbal_teacher_id,
        "math_teacher_id": new_class.math_teacher_id,
        "schedule_template": data.schedule_template,
        "start_date": data.start_date,
        "sessions_created": len(sessions)
    }



@router.get("/student-home/details")
def get_student_home_class_details(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["student"]))
):
    enrollments = db.query(ClassEnrollment).filter(
        ClassEnrollment.student_id == current_user.id
    ).all()
    class_ids = [enrollment.class_id for enrollment in enrollments]
    if not class_ids:
        return []

    classes = (
        db.query(Class)
        .filter(Class.id.in_(class_ids), Class.archived.is_(False))
        .all()
    )
    result = []

    for class_obj in classes:
        verbal_teacher = db.query(User).filter(User.id == class_obj.verbal_teacher_id).first()
        math_teacher = db.query(User).filter(User.id == class_obj.math_teacher_id).first()
        sessions = db.query(ClassSession).filter(ClassSession.class_id == class_obj.id).all()
        session_ids = [s.id for s in sessions]

        created_mock_assignments = ensure_mock_assignments_for_class(db, class_obj.id)
        if created_mock_assignments:
            db.commit()

        assignments = db.query(Assignment).filter(
            Assignment.session_id.in_(session_ids),
            Assignment.student_id == current_user.id
        ).all() if session_ids else []

        attendances = db.query(Attendance).filter(
            Attendance.session_id.in_(session_ids),
            Attendance.student_id == current_user.id
        ).all() if session_ids else []

        result.append({
            "class": {
                "class_id": class_obj.id,
                "name": class_obj.name
            },
            "verbal_teacher": {
                "user_id": verbal_teacher.id,
                "name": verbal_teacher.name,
                "surname": verbal_teacher.surname
            } if verbal_teacher else None,
            "math_teacher": {
                "user_id": math_teacher.id,
                "name": math_teacher.name,
                "surname": math_teacher.surname
            } if math_teacher else None,
            "students": [
                {
                    "user_id": current_user.id,
                    "name": current_user.name,
                    "surname": current_user.surname
                }
            ],
            "sessions": [
                serialize_session(s, db)
                for s in sessions
            ],
            "attendance": [
                {
                    "attendance_id": a.id,
                    "session_id": a.session_id,
                    "student_id": a.student_id,
                    "status": a.status
                }
                for a in attendances
            ],
            "assignments": [
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
                    "photo_required": a.photo_required
                }
                for a in assignments
            ]
        })

    return result



@router.patch("/{class_id}")
def update_class(
    class_id: int,
    data: UpdateClassData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin"]))
):
    class_obj = db.query(Class).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    if data.name is not None:
        class_obj.name = data.name

    if data.verbal_teacher_id is not None:
        verbal_teacher = db.query(User).filter(
            User.id == data.verbal_teacher_id,
            User.role == "teacher"
        ).first()
        if not verbal_teacher:
            raise HTTPException(status_code=404, detail="Verbal teacher not found")
        class_obj.verbal_teacher_id = data.verbal_teacher_id

    if data.math_teacher_id is not None:
        math_teacher = db.query(User).filter(
            User.id == data.math_teacher_id,
            User.role == "teacher"
        ).first()
        if not math_teacher:
            raise HTTPException(status_code=404, detail="Math teacher not found")
        class_obj.math_teacher_id = data.math_teacher_id

    if data.archived is not None:
        class_obj.archived = data.archived

    db.commit()
    db.refresh(class_obj)

    return {
        "message": "Class updated successfully",
        "class_id": class_obj.id,
        "class_name": class_obj.name,
        "verbal_teacher_id": class_obj.verbal_teacher_id,
        "math_teacher_id": class_obj.math_teacher_id,
        "archived": bool(class_obj.archived),
    }


@router.post("/{class_id}/students")
def assign_student_to_class(
    class_id: int,
    data: EnrollmentData,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher"]))
):
    class_obj = db.query(Class).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    student = db.query(User).filter(
        User.id == data.student_id,
        User.role == "student"
    ).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    existing = db.query(ClassEnrollment).filter(
        ClassEnrollment.class_id == class_id,
        ClassEnrollment.student_id == data.student_id
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Student already enrolled in this class")

    enrollment = ClassEnrollment(
        class_id=class_id,
        student_id=data.student_id
    )

    db.add(enrollment)
    created_mock_assignments = ensure_mock_assignments_for_class(db, class_id)
    db.commit()

    return {
        "message": "Student assigned successfully",
        "class_id": class_id,
        "student_id": data.student_id,
        "mock_assignments_created": created_mock_assignments,
    }


@router.get("/{class_id}/detail")
def get_class_full_detail(
    class_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    class_obj = classes_query(db, current_user).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    verbal_teacher = db.query(User).filter(User.id == class_obj.verbal_teacher_id).first()
    math_teacher = db.query(User).filter(User.id == class_obj.math_teacher_id).first()

    enrollments = db.query(ClassEnrollment).filter(
        ClassEnrollment.class_id == class_id
    ).all()
    student_ids = [e.student_id for e in enrollments]

    students = db.query(User).filter(User.id.in_(student_ids)).all() if student_ids else []
    sessions = db.query(ClassSession).filter(ClassSession.class_id == class_id).all()

    session_ids = [s.id for s in sessions]

    created_mock_assignments = ensure_mock_assignments_for_class(db, class_id)
    if created_mock_assignments:
        db.commit()

    attendances = db.query(Attendance).filter(
        Attendance.session_id.in_(session_ids)
    ).all() if session_ids else []

    assignments = db.query(Assignment).filter(
        Assignment.session_id.in_(session_ids)
    ).all() if session_ids else []
    assignment_ids = [a.id for a in assignments]
    homework_result_count = db.query(HomeworkResult).filter(
        HomeworkResult.assignment_id.in_(assignment_ids)
    ).count() if assignment_ids else 0
    mock_result_count = db.query(MockResult).filter(
        MockResult.assignment_id.in_(assignment_ids)
    ).count() if assignment_ids else 0

    return {
        "class": {
            "class_id": class_obj.id,
            "name": class_obj.name
        },
        "verbal_teacher": {
            "user_id": verbal_teacher.id,
            "name": verbal_teacher.name,
            "surname": verbal_teacher.surname
        } if verbal_teacher else None,
        "math_teacher": {
            "user_id": math_teacher.id,
            "name": math_teacher.name,
            "surname": math_teacher.surname
        } if math_teacher else None,
        "students": [
            {
                "user_id": s.id,
                "name": s.name,
                "surname": s.surname
            }
            for s in students
        ],
        "sessions": [
            serialize_session(s, db)
            for s in sessions
        ],
        "attendance": [
            {
                "attendance_id": a.id,
                "session_id": a.session_id,
                "student_id": a.student_id,
                "status": a.status
            }
            for a in attendances
        ],
        "assignments": [
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
                "photo_required": a.photo_required
            }
            for a in assignments
        ],
        "homework_result_count": homework_result_count,
        "mock_result_count": mock_result_count
    }


@router.get("/{class_id}/homework-results")
def get_class_homework_results(
    class_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    class_obj = classes_query(db, current_user).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    sessions = db.query(ClassSession).filter(ClassSession.class_id == class_id).all()
    session_ids = [s.id for s in sessions]
    if not session_ids:
        return []

    assignments = db.query(Assignment).filter(
        Assignment.session_id.in_(session_ids)
    ).all()
    assignment_ids = [a.id for a in assignments]
    if not assignment_ids:
        return []

    assignment_map = {a.id: a for a in assignments}
    results = homework_results_query(db, current_user).filter(
        HomeworkResult.assignment_id.in_(assignment_ids)
    ).all()

    rows: list[dict] = []
    for result in results:
        if result.assignment_id not in assignment_map:
            continue
        student_id = assignment_map[result.assignment_id].student_id
        rows.extend(expand_class_homework_result_rows(result, student_id))
    return rows


@router.get("/{class_id}/mock-results")
def get_class_mock_results(
    class_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    class_obj = classes_query(db, current_user).filter(Class.id == class_id).first()
    if not class_obj:
        raise HTTPException(status_code=404, detail="Class not found")

    sessions = db.query(ClassSession).filter(ClassSession.class_id == class_id).all()
    session_ids = [s.id for s in sessions]
    if not session_ids:
        return []

    assignments = db.query(Assignment).filter(
        Assignment.session_id.in_(session_ids)
    ).all()
    assignment_ids = [a.id for a in assignments]
    if not assignment_ids:
        return []

    results = db.query(MockResult).filter(
        MockResult.assignment_id.in_(assignment_ids)
    ).all()

    return [
        serialize_mock_result_list_item(r)
        for r in results
    ]
