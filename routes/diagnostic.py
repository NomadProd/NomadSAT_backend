from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from dependencies.auth import (
    AuthUser,
    get_current_user,
    is_admin_or_mentor,
    require_admin_or_mentor,
    require_staff,
)
from Methods.auth import get_db
from models import DiagnosticAnswer, DiagnosticAttempt, DiagnosticQuestion
from schemas.diagnostic import (
    ChoiceSchema,
    DiagnosticAnswerSchema,
    DiagnosticAnswerSubmit,
    DiagnosticAttemptCreatedSchema,
    DiagnosticAttemptSchema,
    DiagnosticQuestionAdminSchema,
    DiagnosticQuestionCreate,
    DiagnosticQuestionPublicSchema,
    DiagnosticQuestionUpdate,
)
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_choices(raw) -> list[ChoiceSchema]:
    if not isinstance(raw, list):
        return []
    return [ChoiceSchema.model_validate(item) for item in raw]


def serialize_question_admin(question: DiagnosticQuestion) -> dict:
    payload = DiagnosticQuestionAdminSchema(
        id=question.id,
        section=question.section,
        domain=question.domain,
        difficulty=question.difficulty,
        points=question.points,
        order_index=question.order_index,
        question_text=question.question_text,
        question_image=question.question_image,
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
        question_text=question.question_text,
        question_image=question.question_image,
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
    question.question_text = data.question_text
    question.question_image = data.question_image
    question.choices = [choice.model_dump() for choice in data.choices]
    question.correct_choice = data.correct_choice
    question.explanation = data.explanation
    if existing is None:
        question.created_at = _utcnow()
        question.created_by_id = created_by_id
    return question


def _order_index_taken(
    db: Session,
    order_index: int,
    *,
    exclude_id: int | None = None,
) -> bool:
    query = db.query(DiagnosticQuestion).filter(
        DiagnosticQuestion.order_index == order_index
    )
    existing = query.first()
    if existing is None:
        return False
    if exclude_id is not None and existing.id == exclude_id:
        return False
    return True


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
    if question is None:
        raise HTTPException(status_code=404, detail="Diagnostic question not found")
    if _order_index_taken(db, data.order_index, exclude_id=question.id):
        raise HTTPException(
            status_code=409,
            detail=f"A question already exists for order_index {data.order_index}",
        )
    _question_from_payload(
        data,
        created_by_id=current_user.id,
        existing=question,
    )
    db.commit()
    db.refresh(question)
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
    if question is None:
        raise HTTPException(status_code=404, detail="Diagnostic question not found")
    answer_count = (
        db.query(DiagnosticAnswer)
        .filter(DiagnosticAnswer.question_id == question_id)
        .count()
    )
    if answer_count:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot delete a question that has student answers. "
                "Historical attempts would be corrupted."
            ),
        )
    db.delete(question)
    db.commit()
    return {"ok": True, "id": question_id}


@router.get("/diagnostic/questions")
def list_diagnostic_questions(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    try:
        questions = (
            db.query(DiagnosticQuestion)
            .order_by(DiagnosticQuestion.order_index)
            .all()
        )
        # #region agent log
        try:
            import json
            import time
            with open("/Users/rassulkaa/Desktop/rassulkaa/turan/.cursor/debug-b71801.log", "a", encoding="utf-8") as _dbg:
                _dbg.write(json.dumps({
                    "sessionId": "b71801",
                    "runId": "post-fix",
                    "hypothesisId": "A",
                    "location": "diagnostic.py:list_diagnostic_questions",
                    "message": "listed diagnostic questions",
                    "data": {"count": len(questions), "role": current_user.role},
                    "timestamp": int(time.time() * 1000),
                }) + "\n")
        except Exception:
            pass
        # #endregion
        return [serialize_question_admin(question) for question in questions]
    except Exception as exc:
        # #region agent log
        try:
            import json
            import time
            with open("/Users/rassulkaa/Desktop/rassulkaa/turan/.cursor/debug-b71801.log", "a", encoding="utf-8") as _dbg:
                _dbg.write(json.dumps({
                    "sessionId": "b71801",
                    "hypothesisId": "A",
                    "location": "diagnostic.py:list_diagnostic_questions",
                    "message": "list diagnostic questions failed",
                    "data": {"error_type": type(exc).__name__, "error": str(exc)[:220]},
                    "timestamp": int(time.time() * 1000),
                }) + "\n")
        except Exception:
            pass
        # #endregion
        raise


@router.post("/diagnostic/attempts", response_model=DiagnosticAttemptCreatedSchema)
def create_diagnostic_attempt(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    question_count = db.query(DiagnosticQuestion).count()
    if question_count < QUESTION_COUNT:
        raise HTTPException(
            status_code=409,
            detail=(
                "Diagnostic question bank is incomplete. "
                f"Expected {QUESTION_COUNT} questions, found {question_count}."
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
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return DiagnosticAttemptCreatedSchema(
        attempt_id=attempt.id,
        status=attempt.status,
        started_at=attempt.started_at,
    )


@router.get("/diagnostic/attempts")
def list_diagnostic_attempts(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempts = (
        db.query(DiagnosticAttempt)
        .filter(DiagnosticAttempt.student_id == current_user.id)
        .order_by(DiagnosticAttempt.started_at.desc())
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


@router.get("/diagnostic/attempts/{attempt_id}/questions")
def get_attempt_questions(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = get_attempt_or_404(attempt_id, db)
    require_attempt_access(attempt, current_user)
    questions = (
        db.query(DiagnosticQuestion)
        .order_by(DiagnosticQuestion.order_index)
        .all()
    )
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

    is_correct = data.selected_choice == (question.correct_choice or "").strip().upper()
    now = _utcnow()
    answer = (
        db.query(DiagnosticAnswer)
        .filter(
            DiagnosticAnswer.attempt_id == attempt_id,
            DiagnosticAnswer.question_id == data.question_id,
        )
        .first()
    )
    if answer is None:
        answer = DiagnosticAnswer(
            attempt_id=attempt_id,
            question_id=data.question_id,
        )
        db.add(answer)

    answer.selected_choice = data.selected_choice
    answer.is_correct = is_correct
    answer.answered_at = now
    db.commit()
    db.refresh(answer)
    return {
        "question_id": answer.question_id,
        "selected_choice": answer.selected_choice,
        "answered_at": answer.answered_at,
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

    questions = (
        db.query(DiagnosticQuestion)
        .order_by(DiagnosticQuestion.order_index)
        .all()
    )
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
