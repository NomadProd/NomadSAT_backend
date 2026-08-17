from __future__ import annotations

import logging
import mimetypes
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dependencies.auth import (
    AuthUser,
    get_current_user,
    is_admin_or_mentor,
    normalize_role,
    require_admin_or_mentor,
    require_staff,
)
from Methods.auth import get_db
from models import (
    Class,
    ClassEnrollment,
    DiagnosticAnswer,
    DiagnosticAttempt,
    DiagnosticQuestion,
    User,
)
from schemas.diagnostic import (
    ChoiceSchema,
    DiagnosticAnswerReviewSchema,
    DiagnosticAnswerSchema,
    DiagnosticAnswerSubmit,
    DiagnosticAttemptCreatedSchema,
    DiagnosticAttemptDetailSchema,
    DiagnosticAttemptListItem,
    DiagnosticAttemptProgress,
    DiagnosticAttemptSchema,
    DiagnosticQuestionAdminSchema,
    DiagnosticQuestionCreate,
    DiagnosticQuestionPublicSchema,
    DiagnosticQuestionUpdate,
    DiagnosticStudentSummary,
)
from dependencies.filters import (
    classes_query,
    diagnostic_attempts_query,
    teacher_owns_class,
)
from services.cloudinary_service import delete_file, upload_file
from services.diagnostic_config import (
    QUESTION_COUNT,
    SECTION_MATH,
    SECTION_READING_WRITING,
    SOFT_COMPLETION_LIMIT_MINUTES,
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
)
from services.diagnostic_scoring import estimate_result

logger = logging.getLogger(__name__)

router = APIRouter(tags=["diagnostic"])

MAX_QUESTION_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_QUESTION_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/heic",
}
QUESTION_IMAGE_EXTENSIONS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "heic": "image/heic",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lookup_answer(
    db: Session,
    *,
    attempt_id: int,
    question_id: int,
) -> DiagnosticAnswer | None:
    return (
        db.query(DiagnosticAnswer)
        .filter(
            DiagnosticAnswer.attempt_id == attempt_id,
            DiagnosticAnswer.question_id == question_id,
        )
        .first()
    )


def _apply_answer(
    answer: DiagnosticAnswer,
    *,
    selected_choice: str,
    is_correct: bool,
    answered_at: datetime,
) -> None:
    answer.selected_choice = selected_choice
    answer.is_correct = is_correct
    answer.answered_at = answered_at


def _upsert_diagnostic_answer(
    db: Session,
    *,
    attempt_id: int,
    question_id: int,
    selected_choice: str,
    is_correct: bool,
    answered_at: datetime,
) -> DiagnosticAnswer:
    answer = _lookup_answer(db, attempt_id=attempt_id, question_id=question_id)
    if answer is None:
        answer = DiagnosticAnswer(
            attempt_id=attempt_id,
            question_id=question_id,
        )
        db.add(answer)
    _apply_answer(
        answer,
        selected_choice=selected_choice,
        is_correct=is_correct,
        answered_at=answered_at,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        answer = _lookup_answer(db, attempt_id=attempt_id, question_id=question_id)
        if answer is None:
            raise
        _apply_answer(
            answer,
            selected_choice=selected_choice,
            is_correct=is_correct,
            answered_at=answered_at,
        )
        db.commit()
    db.refresh(answer)
    return answer


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_choices(raw) -> list[ChoiceSchema]:
    if not isinstance(raw, list):
        return []
    return [ChoiceSchema.model_validate(item) for item in raw]


def _resolved_image_scale(question: DiagnosticQuestion) -> float:
    value = getattr(question, "image_scale", None)
    if value is None:
        return 0.85
    return min(1.0, max(0.4, float(value)))


def serialize_question_admin(question: DiagnosticQuestion) -> dict:
    payload = DiagnosticQuestionAdminSchema(
        id=question.id,
        section=question.section,
        domain=question.domain,
        difficulty=question.difficulty,
        points=question.points,
        order_index=question.order_index,
        passage_text=question.passage_text,
        question_text=question.question_text,
        question_url=question.question_url,
        question_image=question.question_image,
        question_image_public_id=question.question_image_public_id,
        image_scale=_resolved_image_scale(question),
        choices=_parse_choices(question.choices),
        correct_choice=question.correct_choice,
        explanation=question.explanation,
        created_at=question.created_at,
        created_by_id=question.created_by_id,
    )
    return payload.model_dump(mode="json")


def serialize_question_public(question: DiagnosticQuestion) -> dict:
    payload = DiagnosticQuestionPublicSchema(
        id=question.id,
        section=question.section,
        domain=question.domain,
        difficulty=question.difficulty,
        order_index=question.order_index,
        passage_text=question.passage_text,
        question_text=question.question_text,
        question_image=question.question_image,
        image_scale=_resolved_image_scale(question),
        choices=_parse_choices(question.choices),
    )
    return payload.model_dump(mode="json")


def serialize_attempt(
    attempt: DiagnosticAttempt,
    answers: list[DiagnosticAnswer],
    *,
    include_correctness: bool,
) -> dict:
    payload = DiagnosticAttemptSchema(
        id=attempt.id,
        student_id=attempt.student_id,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        status=attempt.status,
        rw_points=attempt.rw_points,
        math_points=attempt.math_points,
        rw_scaled_estimate=attempt.rw_scaled_estimate,
        math_scaled_estimate=attempt.math_scaled_estimate,
        total_point_estimate=attempt.total_point_estimate,
        total_range_low=attempt.total_range_low,
        total_range_high=attempt.total_range_high,
        math_started_at=attempt.math_started_at,
        current_question_id=attempt.current_question_id,
        timer_paused_at=getattr(attempt, "timer_paused_at", None),
        timer_pause_seconds=int(getattr(attempt, "timer_pause_seconds", 0) or 0),
        answers=[
            DiagnosticAnswerSchema(
                question_id=answer.question_id,
                selected_choice=answer.selected_choice,
                answered_at=answer.answered_at,
                is_correct=answer.is_correct if include_correctness else None,
            )
            for answer in answers
        ],
    )
    return payload.model_dump(mode="json")


def _can_access_attempt(user: AuthUser, attempt: DiagnosticAttempt) -> bool:
    if attempt.student_id == user.id:
        return True
    return is_admin_or_mentor(user.role)


def _teacher_can_view_student(db: Session, user: AuthUser, student_id: int) -> bool:
    enrollments = (
        db.query(ClassEnrollment)
        .filter(ClassEnrollment.student_id == student_id)
        .all()
    )
    if not enrollments:
        return False
    class_ids = [enrollment.class_id for enrollment in enrollments]
    classes = (
        classes_query(db, user)
        .filter(Class.id.in_(class_ids))
        .all()
    )
    return any(teacher_owns_class(user, class_obj) for class_obj in classes)


def _can_review_attempt(
    db: Session,
    user: AuthUser,
    attempt: DiagnosticAttempt,
) -> bool:
    if attempt.student_id == user.id:
        return True
    if is_admin_or_mentor(user.role):
        return True
    if normalize_role(user.role) == "teacher":
        return _teacher_can_view_student(db, user, attempt.student_id)
    return False


def get_attempt_or_404(attempt_id: int, db: Session) -> DiagnosticAttempt:
    attempt = (
        db.query(DiagnosticAttempt)
        .filter(DiagnosticAttempt.id == attempt_id)
        .first()
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Diagnostic attempt not found")
    return attempt


def require_attempt_access(
    attempt: DiagnosticAttempt,
    user: AuthUser,
) -> None:
    if not _can_access_attempt(user, attempt):
        raise HTTPException(status_code=403, detail="Not enough permissions")


def require_attempt_review_access(
    db: Session,
    attempt: DiagnosticAttempt,
    user: AuthUser,
) -> None:
    if attempt.student_id == user.id:
        return
    scoped = (
        diagnostic_attempts_query(db, user)
        .filter(DiagnosticAttempt.id == attempt.id)
        .first()
    )
    if scoped is None:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    if not _can_review_attempt(db, user, attempt):
        raise HTTPException(status_code=403, detail="Not enough permissions")


def _student_summary(user: User) -> DiagnosticStudentSummary:
    return DiagnosticStudentSummary(
        id=user.id,
        name=user.name,
        surname=user.surname,
    )


def serialize_attempt_list_item(
    attempt: DiagnosticAttempt,
    student: User | None = None,
) -> dict:
    payload = DiagnosticAttemptListItem(
        attempt_id=attempt.id,
        student_id=attempt.student_id,
        status=attempt.status,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        rw_points=attempt.rw_points,
        math_points=attempt.math_points,
        rw_scaled_estimate=attempt.rw_scaled_estimate,
        math_scaled_estimate=attempt.math_scaled_estimate,
        total_point_estimate=attempt.total_point_estimate,
        total_range_low=attempt.total_range_low,
        total_range_high=attempt.total_range_high,
        student=_student_summary(student) if student is not None else None,
    )
    return payload.model_dump(mode="json")


def serialize_attempt_detail(
    attempt: DiagnosticAttempt,
    student: User,
    questions: list[DiagnosticQuestion],
    answers: list[DiagnosticAnswer],
) -> dict:
    answers_by_question = {answer.question_id: answer for answer in answers}
    review_answers: list[DiagnosticAnswerReviewSchema] = []
    for question in questions:
        answer = answers_by_question.get(question.id)
        selected = answer.selected_choice if answer is not None else None
        is_correct = answer.is_correct if answer is not None else None
        explanation = (question.explanation or "").strip() or None
        review_answers.append(
            DiagnosticAnswerReviewSchema(
                order_index=question.order_index,
                section=question.section,
                domain=question.domain,
                difficulty=question.difficulty,
                points=question.points,
                question_text=question.question_text,
                passage_text=question.passage_text,
                question_image=question.question_image,
                image_scale=_resolved_image_scale(question),
                choices=_parse_choices(question.choices),
                selected_choice=selected,
                correct_choice=question.correct_choice,
                is_correct=is_correct,
                explanation=explanation,
            )
        )
    payload = DiagnosticAttemptDetailSchema(
        attempt_id=attempt.id,
        student=_student_summary(student),
        completed_at=attempt.completed_at,
        status=attempt.status,
        rw_points=attempt.rw_points,
        math_points=attempt.math_points,
        rw_scaled_estimate=attempt.rw_scaled_estimate,
        math_scaled_estimate=attempt.math_scaled_estimate,
        total_point_estimate=attempt.total_point_estimate,
        total_range_low=attempt.total_range_low,
        total_range_high=attempt.total_range_high,
        answers=review_answers,
    )
    return payload.model_dump(mode="json")


def _sorted_attempts(attempts: list[DiagnosticAttempt]) -> list[DiagnosticAttempt]:
    return sorted(
        attempts,
        key=lambda item: item.completed_at or item.started_at,
        reverse=True,
    )


def _users_by_id(db: Session, user_ids: list[int]) -> dict[int, User]:
    if not user_ids:
        return {}
    rows = db.query(User).filter(User.id.in_(user_ids)).all()
    return {row.id: row for row in rows}


def _question_from_payload(
    data: DiagnosticQuestionCreate,
    *,
    created_by_id: int | None,
    existing: DiagnosticQuestion | None = None,
) -> DiagnosticQuestion:
    question = existing or DiagnosticQuestion()
    question.section = data.section
    question.domain = data.domain
    question.difficulty = data.difficulty
    question.points = data.points
    question.order_index = data.order_index
    question.passage_text = data.passage_text
    question.question_text = data.question_text
    question.question_url = data.question_url
    question.question_image = data.question_image
    question.question_image_public_id = data.question_image_public_id
    question.image_scale = data.image_scale
    question.choices = [choice.model_dump() for choice in data.choices]
    question.correct_choice = data.correct_choice
    question.explanation = data.explanation
    if existing is None:
        question.created_at = _utcnow()
        question.created_by_id = created_by_id
    return question


def _parse_question_ids(raw) -> list[int]:
    if not isinstance(raw, list):
        return []
    ids: list[int] = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _question_ids_from(questions: list[DiagnosticQuestion]) -> list[int]:
    return [question.id for question in questions]


def _active_questions(db: Session) -> list[DiagnosticQuestion]:
    return (
        db.query(DiagnosticQuestion)
        .filter(DiagnosticQuestion.deleted_at.is_(None))
        .order_by(DiagnosticQuestion.order_index)
        .all()
    )


def _questions_for_ids(db: Session, question_ids: list[int]) -> list[DiagnosticQuestion]:
    if not question_ids:
        return []
    rows = (
        db.query(DiagnosticQuestion)
        .filter(DiagnosticQuestion.id.in_(question_ids))
        .all()
    )
    by_id = {row.id: row for row in rows}
    return [by_id[question_id] for question_id in question_ids if question_id in by_id]


def _questions_for_attempt(
    db: Session,
    attempt: DiagnosticAttempt,
) -> list[DiagnosticQuestion]:
    ids = _parse_question_ids(getattr(attempt, "question_ids", None))
    if ids:
        return _questions_for_ids(db, ids)
    return _active_questions(db)


def _freeze_attempt_questions(
    db: Session,
    questions: list[DiagnosticQuestion],
) -> None:
    ids = _question_ids_from(questions)
    attempts = (
        db.query(DiagnosticAttempt)
        .filter(DiagnosticAttempt.question_ids.is_(None))
        .all()
    )
    for attempt in attempts:
        attempt.question_ids = ids


def _question_belongs_to_attempt(
    attempt: DiagnosticAttempt,
    question_id: int,
) -> bool:
    ids = _parse_question_ids(getattr(attempt, "question_ids", None))
    if not ids:
        return True
    return question_id in ids


def _order_index_taken(
    db: Session,
    order_index: int,
    *,
    exclude_id: int | None = None,
) -> bool:
    query = db.query(DiagnosticQuestion).filter(
        DiagnosticQuestion.order_index == order_index,
        DiagnosticQuestion.deleted_at.is_(None),
    )
    existing = query.first()
    if existing is None:
        return False
    if exclude_id is not None and existing.id == exclude_id:
        return False
    return True


def _question_url_taken(
    db: Session,
    question_url: str | None,
    *,
    exclude_id: int | None = None,
) -> bool:
    if not question_url:
        return False
    query = db.query(DiagnosticQuestion).filter(
        DiagnosticQuestion.question_url == question_url,
        DiagnosticQuestion.deleted_at.is_(None),
    )
    existing = query.first()
    if existing is None:
        return False
    if exclude_id is not None and existing.id == exclude_id:
        return False
    return True


def _question_image_content_type(filename: str, reported: str | None) -> str | None:
    normalized = (reported or "").split(";", 1)[0].strip().lower()
    if normalized in ALLOWED_QUESTION_IMAGE_TYPES:
        return normalized
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in QUESTION_IMAGE_EXTENSIONS:
        return QUESTION_IMAGE_EXTENSIONS[ext]
    guessed, _ = mimetypes.guess_type(filename)
    if guessed and guessed in ALLOWED_QUESTION_IMAGE_TYPES:
        return guessed
    return None


@router.post("/diagnostic/questions/image")
async def upload_diagnostic_question_image(
    file: UploadFile = File(...),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    filename = file.filename or "question.png"
    content_type = _question_image_content_type(filename, file.content_type)
    if content_type is None:
        raise HTTPException(
            status_code=422,
            detail="Upload a JPEG, PNG, GIF, WEBP, or HEIC image",
        )
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="The image file is empty")
    if len(payload) > MAX_QUESTION_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail="File size cannot exceed 10mb")
    uploaded = upload_file(
        payload,
        result_id=current_user.id,
        filename=filename,
        content_type=content_type,
        folder="diagnostic_questions",
        public_id_prefix="diagnostic",
    )
    return {
        "url": uploaded["url"],
        "public_id": uploaded["public_id"],
    }


@router.post("/diagnostic/questions")
def create_diagnostic_question(
    data: DiagnosticQuestionCreate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    if _order_index_taken(db, data.order_index):
        raise HTTPException(
            status_code=409,
            detail=f"A question already exists for order_index {data.order_index}",
        )
    if _question_url_taken(db, data.question_url):
        raise HTTPException(
            status_code=409,
            detail="A question with this URL already exists",
        )
    question = _question_from_payload(data, created_by_id=current_user.id)
    db.add(question)
    db.commit()
    db.refresh(question)
    return serialize_question_admin(question)


@router.put("/diagnostic/questions/{question_id}")
def update_diagnostic_question(
    question_id: int,
    data: DiagnosticQuestionUpdate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    question = (
        db.query(DiagnosticQuestion)
        .filter(DiagnosticQuestion.id == question_id)
        .first()
    )
    if question is None or question.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Diagnostic question not found")
    if _order_index_taken(db, data.order_index, exclude_id=question.id):
        raise HTTPException(
            status_code=409,
            detail=f"A question already exists for order_index {data.order_index}",
        )
    if _question_url_taken(db, data.question_url, exclude_id=question.id):
        raise HTTPException(
            status_code=409,
            detail="A question with this URL already exists",
        )
    old_public_id = question.question_image_public_id
    _question_from_payload(
        data,
        created_by_id=current_user.id,
        existing=question,
    )
    db.commit()
    db.refresh(question)
    if old_public_id and old_public_id != question.question_image_public_id:
        delete_file(old_public_id, "image/jpeg")
    return serialize_question_admin(question)


@router.delete("/diagnostic/questions/{question_id}")
def delete_diagnostic_question(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_admin_or_mentor),
):
    question = (
        db.query(DiagnosticQuestion)
        .filter(DiagnosticQuestion.id == question_id)
        .first()
    )
    if question is None or question.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Diagnostic question not found")
    active = _active_questions(db)
    _freeze_attempt_questions(db, active)
    question.deleted_at = _utcnow()
    db.commit()
    return {"ok": True, "id": question_id}


@router.get("/diagnostic/questions")
def list_diagnostic_questions(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    questions = _active_questions(db)
    return [serialize_question_admin(question) for question in questions]


@router.post("/diagnostic/attempts", response_model=DiagnosticAttemptCreatedSchema)
def create_diagnostic_attempt(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    questions = _active_questions(db)
    if len(questions) < QUESTION_COUNT:
        raise HTTPException(
            status_code=409,
            detail=(
                "Diagnostic question bank is incomplete. "
                f"Expected {QUESTION_COUNT} questions, found {len(questions)}."
            ),
        )

    in_progress = (
        db.query(DiagnosticAttempt)
        .filter(
            DiagnosticAttempt.student_id == current_user.id,
            DiagnosticAttempt.status == STATUS_IN_PROGRESS,
        )
        .all()
    )
    for attempt in in_progress:
        attempt.status = STATUS_ABANDONED

    attempt = DiagnosticAttempt(
        student_id=current_user.id,
        started_at=_utcnow(),
        status=STATUS_IN_PROGRESS,
        question_ids=_question_ids_from(questions),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return DiagnosticAttemptCreatedSchema(
        attempt_id=attempt.id,
        status=attempt.status,
        started_at=attempt.started_at,
    )


def _list_attempts_for_student(
    db: Session,
    user: AuthUser,
    student_id: int,
    *,
    include_student: bool,
) -> list[dict]:
    if normalize_role(user.role) == "student" and student_id != user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    if normalize_role(user.role) == "teacher" and not _teacher_can_view_student(
        db, user, student_id
    ):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    if (
        normalize_role(user.role) not in ("admin", "mentor", "teacher", "student")
    ):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    attempts = _sorted_attempts(
        diagnostic_attempts_query(db, user)
        .filter(DiagnosticAttempt.student_id == student_id)
        .all()
    )
    student = None
    if include_student:
        student = db.query(User).filter(User.id == student_id).first()
    return [
        serialize_attempt_list_item(attempt, student=student)
        for attempt in attempts
    ]


@router.get("/diagnostic/attempts/me")
def list_my_diagnostic_attempts(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    if normalize_role(current_user.role) != "student":
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return _list_attempts_for_student(
        db,
        current_user,
        current_user.id,
        include_student=False,
    )


@router.get("/diagnostic/attempts")
def list_diagnostic_attempts(
    student_id: int | None = None,
    class_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    role = normalize_role(current_user.role)
    if student_id is not None and class_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide either student_id or class_id, not both",
        )

    if student_id is not None:
        if role == "student" and student_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")
        if role not in ("admin", "mentor", "teacher", "student"):
            raise HTTPException(status_code=403, detail="Not enough permissions")
        return _list_attempts_for_student(
            db,
            current_user,
            student_id,
            include_student=role != "student",
        )

    if class_id is not None:
        if role not in ("admin", "mentor", "teacher"):
            raise HTTPException(status_code=403, detail="Not enough permissions")
        class_obj = db.query(Class).filter(Class.id == class_id).first()
        if class_obj is None:
            raise HTTPException(status_code=404, detail="Class not found")
        if not teacher_owns_class(current_user, class_obj):
            raise HTTPException(status_code=403, detail="Not enough permissions")
        enrollments = (
            db.query(ClassEnrollment)
            .filter(ClassEnrollment.class_id == class_id)
            .all()
        )
        student_ids = [enrollment.student_id for enrollment in enrollments]
        if not student_ids:
            return []
        attempts = _sorted_attempts(
            diagnostic_attempts_query(db, current_user)
            .filter(DiagnosticAttempt.student_id.in_(student_ids))
            .all()
        )
        students = _users_by_id(db, student_ids)
        return [
            serialize_attempt_list_item(
                attempt,
                student=students.get(attempt.student_id),
            )
            for attempt in attempts
        ]

    attempts = _sorted_attempts(
        db.query(DiagnosticAttempt)
        .filter(DiagnosticAttempt.student_id == current_user.id)
        .all()
    )
    payloads = []
    for attempt in attempts:
        answers = (
            db.query(DiagnosticAnswer)
            .filter(DiagnosticAnswer.attempt_id == attempt.id)
            .all()
        )
        payloads.append(
            serialize_attempt(
                attempt,
                answers,
                include_correctness=attempt.status == STATUS_COMPLETED,
            )
        )
    return payloads


@router.get("/diagnostic/attempts/{attempt_id}/detail")
def get_diagnostic_attempt_detail(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = get_attempt_or_404(attempt_id, db)
    require_attempt_review_access(db, attempt, current_user)
    if attempt.status != STATUS_COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Review is only available for completed attempts",
        )
    student = db.query(User).filter(User.id == attempt.student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    questions = _questions_for_attempt(db, attempt)
    answers = (
        db.query(DiagnosticAnswer)
        .filter(DiagnosticAnswer.attempt_id == attempt_id)
        .all()
    )
    return serialize_attempt_detail(attempt, student, questions, answers)


@router.get("/diagnostic/attempts/{attempt_id}/questions")
def get_attempt_questions(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = get_attempt_or_404(attempt_id, db)
    require_attempt_access(attempt, current_user)
    questions = _questions_for_attempt(db, attempt)
    return [serialize_question_public(question) for question in questions]


@router.post("/diagnostic/attempts/{attempt_id}/answers")
def submit_diagnostic_answer(
    attempt_id: int,
    data: DiagnosticAnswerSubmit,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = get_attempt_or_404(attempt_id, db)
    require_attempt_access(attempt, current_user)
    if attempt.student_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the attempt owner can submit answers",
        )
    if attempt.status != STATUS_IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail="This diagnostic attempt is no longer in progress",
        )

    question = (
        db.query(DiagnosticQuestion)
        .filter(DiagnosticQuestion.id == data.question_id)
        .first()
    )
    if question is None:
        raise HTTPException(status_code=404, detail="Diagnostic question not found")
    if not _question_belongs_to_attempt(attempt, question.id):
        raise HTTPException(status_code=404, detail="Diagnostic question not found")

    is_correct = data.selected_choice == (question.correct_choice or "").strip().upper()
    now = _utcnow()
    answer = _upsert_diagnostic_answer(
        db,
        attempt_id=attempt_id,
        question_id=data.question_id,
        selected_choice=data.selected_choice,
        is_correct=is_correct,
        answered_at=now,
    )
    return {
        "question_id": answer.question_id,
        "selected_choice": answer.selected_choice,
        "answered_at": answer.answered_at,
    }


@router.patch("/diagnostic/attempts/{attempt_id}/progress")
def save_diagnostic_progress(
    attempt_id: int,
    data: DiagnosticAttemptProgress,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = get_attempt_or_404(attempt_id, db)
    require_attempt_access(attempt, current_user)
    if attempt.student_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the attempt owner can save progress",
        )
    if attempt.status != STATUS_IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail="This diagnostic attempt is no longer in progress",
        )

    question = (
        db.query(DiagnosticQuestion)
        .filter(DiagnosticQuestion.id == data.current_question_id)
        .first()
    )
    if question is None:
        raise HTTPException(status_code=404, detail="Diagnostic question not found")
    if not _question_belongs_to_attempt(attempt, question.id):
        raise HTTPException(status_code=404, detail="Diagnostic question not found")

    attempt.current_question_id = data.current_question_id
    if data.math_started_at is not None and attempt.math_started_at is None:
        attempt.math_started_at = _as_utc(data.math_started_at)
        attempt.timer_paused_at = None
        attempt.timer_pause_seconds = 0

    if data.pause_timer is True:
        if getattr(attempt, "timer_paused_at", None) is None:
            attempt.timer_paused_at = datetime.now(timezone.utc)
    elif data.pause_timer is False:
        paused_at = getattr(attempt, "timer_paused_at", None)
        if paused_at is not None:
            extra = int(
                (datetime.now(timezone.utc) - _as_utc(paused_at)).total_seconds()
            )
            if extra < 0:
                extra = 0
            attempt.timer_pause_seconds = (
                int(getattr(attempt, "timer_pause_seconds", 0) or 0) + extra
            )
            attempt.timer_paused_at = None

    db.commit()
    db.refresh(attempt)
    return {
        "current_question_id": attempt.current_question_id,
        "math_started_at": attempt.math_started_at,
        "timer_paused_at": attempt.timer_paused_at,
        "timer_pause_seconds": int(getattr(attempt, "timer_pause_seconds", 0) or 0),
    }


@router.post("/diagnostic/attempts/{attempt_id}/complete")
def complete_diagnostic_attempt(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = get_attempt_or_404(attempt_id, db)
    require_attempt_access(attempt, current_user)
    if attempt.student_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the attempt owner can complete this attempt",
        )
    if attempt.status == STATUS_COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="This diagnostic attempt is already completed",
        )
    if attempt.status != STATUS_IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail="This diagnostic attempt is no longer in progress",
        )

    elapsed = _utcnow() - _as_utc(attempt.started_at)
    if elapsed > timedelta(minutes=SOFT_COMPLETION_LIMIT_MINUTES):
        logger.warning(
            "Diagnostic attempt %s completed after %s (soft limit %s minutes)",
            attempt.id,
            elapsed,
            SOFT_COMPLETION_LIMIT_MINUTES,
        )

    questions = _questions_for_attempt(db, attempt)
    answers = (
        db.query(DiagnosticAnswer)
        .filter(DiagnosticAnswer.attempt_id == attempt_id)
        .all()
    )
    correct_by_question = {
        answer.question_id: bool(answer.is_correct) for answer in answers
    }

    rw_points = 0
    math_points = 0
    for question in questions:
        if not correct_by_question.get(question.id):
            continue
        if question.section == SECTION_READING_WRITING:
            rw_points += question.points
        elif question.section == SECTION_MATH:
            math_points += question.points

    estimate = estimate_result(rw_points, math_points)
    attempt.rw_points = rw_points
    attempt.math_points = math_points
    attempt.rw_scaled_estimate = estimate["rw_scaled_estimate"]
    attempt.math_scaled_estimate = estimate["math_scaled_estimate"]
    attempt.total_point_estimate = estimate["total_point_estimate"]
    attempt.total_range_low = estimate["total_range_low"]
    attempt.total_range_high = estimate["total_range_high"]
    attempt.completed_at = _utcnow()
    attempt.status = STATUS_COMPLETED
    db.commit()
    db.refresh(attempt)
    return serialize_attempt(attempt, answers, include_correctness=True)


@router.get("/diagnostic/attempts/{attempt_id}")
def get_diagnostic_attempt(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = get_attempt_or_404(attempt_id, db)
    require_attempt_access(attempt, current_user)
    answers = (
        db.query(DiagnosticAnswer)
        .filter(DiagnosticAnswer.attempt_id == attempt_id)
        .all()
    )
    return serialize_attempt(
        attempt,
        answers,
        include_correctness=attempt.status == STATUS_COMPLETED,
    )
