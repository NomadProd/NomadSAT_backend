from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import UploadFile
from sqlalchemy.orm import Session

from cloudinary_upload import upload_homework_file
from models import (
    Assignment,
    HomeworkResult,
    MockResult,
    User,
    Attendance,
    Session as ClassSession,
    Class,
    ClassEnrollment
)
from dependencies.auth import AuthUser, get_current_user
from dependencies.filters import assignments_query, homework_results_query
from Methods.auth import get_db, require_roles
from schemas import (
    CreateMockResultData,
    UpdateMockResultData
)

router = APIRouter(tags=["results"])


def calc_accuracy(correct, incorrect):
    if correct is None or incorrect is None:
        return None
    total = correct + incorrect
    if total == 0:
        return None
    return round(correct * 100 / total, 2)


def normalize_session_plan_item_ids(raw_value) -> list[int]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [int(value) for value in raw_value if value is not None]
    if isinstance(raw_value, tuple):
        return [int(value) for value in raw_value if value is not None]
    return [int(raw_value)]


def serialize_history_context(assignment: Assignment | None, db: Session):
    if assignment is None:
        return None, None, None

    session_obj = db.query(ClassSession).filter(
        ClassSession.id == assignment.session_id
    ).first()
    class_obj = None
    if session_obj:
        class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()

    assignment_payload = {
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
    session_payload = None
    if session_obj:
        plan_item_ids = normalize_session_plan_item_ids(
            session_obj.academic_plan_item_id
        )
        session_payload = {
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
            "lesson_notes": session_obj.lesson_notes,
        }
    class_payload = None
    if class_obj:
        class_payload = {
            "class_id": class_obj.id,
            "class_name": class_obj.name,
            "verbal_teacher_id": class_obj.verbal_teacher_id,
            "math_teacher_id": class_obj.math_teacher_id,
        }

    return assignment_payload, session_payload, class_payload


def can_access_history_class(current_user: User, class_payload):
    if current_user.role != "teacher":
        return True
    if class_payload is None:
        return False
    allowed_teacher_ids = [
        class_payload.get("verbal_teacher_id"),
        class_payload.get("math_teacher_id"),
    ]
    return current_user.id in allowed_teacher_ids


def check_teacher_access(current_user: User, class_obj: Class):
    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")


def parse_optional_bool(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_optional_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Numeric fields must be integers") from exc


async def parse_homework_payload(request: Request, assignment_id: int | None = None):
    content_type = request.headers.get("content-type", "")
    uploaded_file = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        uploaded_file = form.get("photo") or form.get("file")
        if uploaded_file is not None and not isinstance(uploaded_file, UploadFile):
            uploaded_file = None

        submitted = parse_optional_bool(form.get("submitted"))
        photo_link = form.get("photo_link") or None
        correct_total = parse_optional_int(form.get("correct_total"))
        incorrect_total = parse_optional_int(form.get("incorrect_total"))
        analysis = form.get("analysis") or None
    else:
        body = await request.json()
        submitted = parse_optional_bool(body.get("submitted"))
        photo_link = body.get("photo_link")
        correct_total = parse_optional_int(body.get("correct_total"))
        incorrect_total = parse_optional_int(body.get("incorrect_total"))
        analysis = body.get("analysis")

    if uploaded_file is not None:
        if assignment_id is None:
            assignment_id = 0
        photo_link = await upload_homework_file(uploaded_file, assignment_id=assignment_id)

    return {
        "submitted": submitted,
        "photo_link": photo_link,
        "correct_total": correct_total,
        "incorrect_total": incorrect_total,
        "analysis": analysis,
    }


async def parse_mock_payload(request: Request, assignment_id: int | None = None):
    content_type = request.headers.get("content-type", "")
    uploaded_file = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        uploaded_file = form.get("photo") or form.get("file")
        if uploaded_file is not None and not isinstance(uploaded_file, UploadFile):
            uploaded_file = None

        student_id = parse_optional_int(form.get("student_id"))
        submitted = parse_optional_bool(form.get("submitted"))
        verbal_points = parse_optional_int(form.get("verbal_points"))
        math_points = parse_optional_int(form.get("math_points"))
        verbal_incorrect = parse_optional_int(form.get("verbal_incorrect"))
        math_incorrect = parse_optional_int(form.get("math_incorrect"))
        weak_areas = form.get("weak_areas") or None
        photo_link = form.get("photo_link") or None
    else:
        body = await request.json()
        student_id = parse_optional_int(body.get("student_id"))
        submitted = parse_optional_bool(body.get("submitted"))
        verbal_points = parse_optional_int(body.get("verbal_points"))
        math_points = parse_optional_int(body.get("math_points"))
        verbal_incorrect = parse_optional_int(body.get("verbal_incorrect"))
        math_incorrect = parse_optional_int(body.get("math_incorrect"))
        weak_areas = body.get("weak_areas")
        photo_link = body.get("photo_link")

    if uploaded_file is not None:
        if assignment_id is None:
            assignment_id = 0
        photo_link = await upload_homework_file(uploaded_file, assignment_id=assignment_id)

    return {
        "student_id": student_id,
        "submitted": submitted,
        "verbal_points": verbal_points,
        "math_points": math_points,
        "verbal_incorrect": verbal_incorrect,
        "math_incorrect": math_incorrect,
        "weak_areas": weak_areas,
        "photo_link": photo_link,
    }


@router.post("/assignments/{assignment_id}/mock-results")
async def create_mock_result(
    assignment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher", "student"]))
):
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    session_obj = db.query(ClassSession).filter(ClassSession.id == assignment.session_id).first()
    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()

    if session_obj.session_type != "mock":
        raise HTTPException(status_code=400, detail="This assignment is not for mock results")

    payload = await parse_mock_payload(request, assignment_id=assignment_id)
    student_id = payload.get("student_id")
    if student_id is None:
        raise HTTPException(status_code=400, detail="student_id is required")

    enrollment = db.query(ClassEnrollment).filter(
        ClassEnrollment.class_id == class_obj.id,
        ClassEnrollment.student_id == student_id
    ).first()
    if not enrollment:
        raise HTTPException(status_code=400, detail="Student is not enrolled in this class")

    check_teacher_access(current_user, class_obj)

    if current_user.role == "student" and current_user.id != student_id:
        raise HTTPException(status_code=403, detail="Students can submit only their own results")

    existing = db.query(MockResult).filter(
        MockResult.assignment_id == assignment_id,
        MockResult.student_id == student_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Mock result already exists")

    total_points = None
    if payload["verbal_points"] is not None and payload["math_points"] is not None:
        total_points = payload["verbal_points"] + payload["math_points"]

    result = MockResult(
        assignment_id=assignment_id,
        student_id=student_id,
        submitted=payload["submitted"],
        total_points=total_points,
        verbal_points=payload["verbal_points"],
        math_points=payload["math_points"],
        verbal_incorrect=payload["verbal_incorrect"],
        math_incorrect=payload["math_incorrect"],
        weak_areas=payload["weak_areas"],
        photo_link=payload["photo_link"]
    )

    db.add(result)
    db.commit()
    db.refresh(result)

    return {
        "message": "Mock result created successfully",
        "result_id": result.id,
        "assignment_id": result.assignment_id,
        "student_id": result.student_id
    }


@router.get("/assignments/{assignment_id}/mock-results")
def get_mock_results_by_assignment(
    assignment_id: int,
    db: Session = Depends(get_db)
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

    results = db.query(MockResult).filter(
        MockResult.assignment_id == assignment_id
    ).all()

    return [
        {
            "result_id": r.id,
            "assignment_id": r.assignment_id,
            "student_id": r.student_id,
            "submitted": r.submitted,
            "total_points": r.total_points,
            "verbal_points": r.verbal_points,
            "math_points": r.math_points,
            "verbal_incorrect": r.verbal_incorrect,
            "math_incorrect": r.math_incorrect,
            "weak_areas": r.weak_areas,
            "photo_link": r.photo_link
        }
        for r in results
    ]


@router.patch("/mock-results/{result_id}")
async def update_mock_result(
    result_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher", "student"]))
):
    result = db.query(MockResult).filter(MockResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Mock result not found")

    assignment = db.query(Assignment).filter(Assignment.id == result.assignment_id).first()
    session_obj = db.query(ClassSession).filter(ClassSession.id == assignment.session_id).first()
    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()

    check_teacher_access(current_user, class_obj)

    if current_user.role == "student" and current_user.id != result.student_id:
        raise HTTPException(status_code=403, detail="Students can edit only their own results")

    payload = await parse_mock_payload(request, assignment_id=result.assignment_id)

    if payload["submitted"] is not None:
        result.submitted = payload["submitted"]
    if payload["verbal_points"] is not None:
        result.verbal_points = payload["verbal_points"]
    if payload["math_points"] is not None:
        result.math_points = payload["math_points"]
    if payload["verbal_incorrect"] is not None:
        result.verbal_incorrect = payload["verbal_incorrect"]
    if payload["math_incorrect"] is not None:
        result.math_incorrect = payload["math_incorrect"]
    if payload["weak_areas"] is not None:
        result.weak_areas = payload["weak_areas"]
    if payload["photo_link"] is not None:
        result.photo_link = payload["photo_link"]

    if result.verbal_points is not None and result.math_points is not None:
        result.total_points = result.verbal_points + result.math_points
    else:
        result.total_points = None

    db.commit()
    db.refresh(result)

    return {
        "message": "Mock result updated successfully",
        "result_id": result.id
    }


@router.get("/students/{student_id}/mock-results")
def get_student_mock_history(
    student_id: int,
    db: Session = Depends(get_db)
):
    student = db.query(User).filter(User.id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    results = db.query(MockResult).filter(MockResult.student_id == student_id).all()

    history = []
    for r in results:
        assignment = db.query(Assignment).filter(Assignment.id == r.assignment_id).first()
        assignment_payload, session_payload, class_payload = serialize_history_context(
            assignment,
            db,
        )
        history.append({
            "result_id": r.id,
            "assignment_id": r.assignment_id,
            "student_id": r.student_id,
            "submitted": r.submitted,
            "total_points": r.total_points,
            "verbal_points": r.verbal_points,
            "math_points": r.math_points,
            "verbal_incorrect": r.verbal_incorrect,
            "math_incorrect": r.math_incorrect,
            "weak_areas": r.weak_areas,
            "photo_link": r.photo_link,
            "assignment": assignment_payload,
            "session": session_payload,
            "class": class_payload,
        })

    return history


@router.get("/students/{student_id}/results")
def get_student_all_results(
    student_id: int,
    db: Session = Depends(get_db)
):
    student = db.query(User).filter(User.id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    homework_results = db.query(HomeworkResult).filter(
        HomeworkResult.student_id == student_id
    ).all()

    mock_results = db.query(MockResult).filter(
        MockResult.student_id == student_id
    ).all()

    attendance_records = db.query(Attendance).filter(
        Attendance.student_id == student_id
    ).all()

    return {
        "student": {
            "user_id": student.id,
            "name": student.name,
            "surname": student.surname
        },
        "homework_results": [
            {
                "result_id": r.id,
                "assignment_id": r.assignment_id,
                "submitted": r.submitted,
                "submitted_at": r.submitted_at,
                "photo_link": r.photo_link,
                "correct_total": r.correct_total,
                "incorrect_total": r.incorrect_total,
                "analysis": r.analysis,
                "accuracy": calc_accuracy(r.correct_total, r.incorrect_total)
            }
            for r in homework_results
        ],
        "mock_results": [
            {
                "result_id": r.id,
                "assignment_id": r.assignment_id,
                "submitted": r.submitted,
                "total_points": r.total_points,
                "verbal_points": r.verbal_points,
                "math_points": r.math_points,
                "verbal_incorrect": r.verbal_incorrect,
                "math_incorrect": r.math_incorrect,
                "weak_areas": r.weak_areas,
                "photo_link": r.photo_link
            }
            for r in mock_results
        ],
        "attendance": [
            {
                "attendance_id": a.id,
                "session_id": a.session_id,
                "status": a.status
            }
            for a in attendance_records
        ]
    }


def check_teacher_access(current_user: User, class_obj: Class):
    if current_user.role == "teacher":
        allowed_teacher_ids = [class_obj.verbal_teacher_id, class_obj.math_teacher_id]
        if current_user.id not in allowed_teacher_ids:
            raise HTTPException(status_code=403, detail="Not enough permissions")


@router.post("/assignments/{assignment_id}/homework-results")
async def create_homework_result(
    assignment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher", "student"]))
):
    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    session_obj = db.query(ClassSession).filter(ClassSession.id == assignment.session_id).first()
    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()

    if session_obj.session_type == "mock":
        raise HTTPException(status_code=400, detail="This assignment is for mock results")

    check_teacher_access(current_user, class_obj)

    if current_user.role == "student" and current_user.id != assignment.student_id:
        raise HTTPException(status_code=403, detail="Students can submit only their own results")

    data = await parse_homework_payload(request, assignment_id)

    existing = db.query(HomeworkResult).filter(
        HomeworkResult.assignment_id == assignment_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Homework result already exists")

    submitted = data["submitted"] if data["submitted"] is not None else False
    submitted_at = datetime.utcnow() if submitted else None

    result = HomeworkResult(
        assignment_id=assignment_id,
        submitted=submitted,
        submitted_at=submitted_at,
        photo_link=data["photo_link"],
        correct_total=data["correct_total"],
        incorrect_total=data["incorrect_total"],
        analysis=data["analysis"]
    )

    db.add(result)
    db.commit()
    db.refresh(result)

    return {
        "message": "Homework result created successfully",
        "result_id": result.id,
        "assignment_id": result.assignment_id,
        "student_id": assignment.student_id,
        "photo_link": result.photo_link
    }


@router.get("/assignments/{assignment_id}/homework-results")
def get_homework_results_by_assignment(
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

    result = (
        homework_results_query(db, current_user)
        .filter(HomeworkResult.assignment_id == assignment_id)
        .first()
    )

    if not result:
        return []

    return [
        {
            "result_id": result.id,
            "assignment_id": result.assignment_id,
            "student_id": assignment.student_id,
            "submitted": result.submitted,
            "submitted_at": result.submitted_at,
            "photo_link": result.photo_link,
            "correct_total": result.correct_total,
            "incorrect_total": result.incorrect_total,
            "analysis": result.analysis,
            "accuracy": calc_accuracy(result.correct_total, result.incorrect_total)
        }
    ]


@router.patch("/homework-results/{result_id}")
async def update_homework_result(
    result_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher", "student"]))
):
    result = db.query(HomeworkResult).filter(HomeworkResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Homework result not found")

    assignment = db.query(Assignment).filter(Assignment.id == result.assignment_id).first()
    session_obj = db.query(ClassSession).filter(ClassSession.id == assignment.session_id).first()
    class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()

    check_teacher_access(current_user, class_obj)

    if current_user.role == "student" and current_user.id != assignment.student_id:
        raise HTTPException(status_code=403, detail="Students can edit only their own results")

    data = await parse_homework_payload(request, result.assignment_id)

    if data["submitted"] is not None:
        result.submitted = data["submitted"]
        if data["submitted"]:
            if result.submitted_at is None:
                result.submitted_at = datetime.utcnow()
        else:
            result.submitted_at = None

    if data["photo_link"] is not None:
        result.photo_link = data["photo_link"]
    if data["correct_total"] is not None:
        result.correct_total = data["correct_total"]
    if data["incorrect_total"] is not None:
        result.incorrect_total = data["incorrect_total"]
    if data["analysis"] is not None:
        result.analysis = data["analysis"]

    db.commit()
    db.refresh(result)

    return {
        "message": "Homework result updated successfully",
        "result_id": result.id,
        "photo_link": result.photo_link
    }


@router.get("/students/{student_id}/homework-results")
def get_student_homework_history(
    student_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    if current_user.role == "student" and current_user.id != student_id:
        raise HTTPException(status_code=404, detail="Student not found")

    student = db.query(User).filter(User.id == student_id, User.role == "student").first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    assignments = (
        assignments_query(db, current_user)
        .filter(Assignment.student_id == student_id)
        .all()
    )
    assignment_ids = [assignment.id for assignment in assignments]

    if not assignment_ids:
        return []

    results = (
        homework_results_query(db, current_user)
        .filter(HomeworkResult.assignment_id.in_(assignment_ids))
        .all()
    )
    assignment_map = {a.id: a for a in assignments}

    history = []
    for r in results:
        assignment = assignment_map.get(r.assignment_id)
        assignment_payload, session_payload, class_payload = serialize_history_context(
            assignment,
            db,
        )
        history.append({
            "result_id": r.id,
            "assignment_id": r.assignment_id,
            "student_id": assignment.student_id if assignment else student_id,
            "submitted": r.submitted,
            "submitted_at": r.submitted_at,
            "photo_link": r.photo_link,
            "correct_total": r.correct_total,
            "incorrect_total": r.incorrect_total,
            "analysis": r.analysis,
            "accuracy": calc_accuracy(r.correct_total, r.incorrect_total),
            "assignment": assignment_payload,
            "session": session_payload,
            "class": class_payload,
        })

    return history
