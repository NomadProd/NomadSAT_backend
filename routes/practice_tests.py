"""Practice tests -- authoring half.

Standalone feature: shares no tables or routes with the paper-mock flow
(mock_results/assignments) or with the hardcoded diagnostic test. Follows
routes/diagnostic.py as its pattern: one APIRouter, full paths per decorator,
auth from dependencies/auth.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from dependencies.auth import (
    AuthUser,
    get_current_user,
    is_admin_or_mentor,
    normalize_role,
    require_staff,
)
from dependencies.filters import classes_query, teacher_owns_class
from Methods.auth import get_db
from models import (
    Class,
    ClassEnrollment,
    PracticeTest,
    PracticeTestAnswer,
    PracticeTestAttempt,
    PracticeTestClass,
    PracticeTestModule,
    PracticeTestQuestion,
    User,
)
from schemas.practice_test import (
    ChoiceSchema,
    PracticeTestAnswerSchema,
    PracticeTestAnswerSubmit,
    PracticeTestAttemptCreated,
    PracticeTestAttemptDetail,
    PracticeTestAttemptSchema,
    PracticeTestClassesUpdate,
    PracticeTestCreate,
    PracticeTestModuleSchema,
    PracticeTestProgressUpdate,
    PracticeTestQuestionAdminSchema,
    PracticeTestQuestionCreate,
    PracticeTestQuestionPublicSchema,
    PracticeTestQuestionUpdate,
    PracticeTestReviewItem,
    PracticeTestSchema,
    PracticeTestStudentSummary,
    PracticeTestUpdate,
    PracticeTestVisibilityUpdate,
)
from services.attachments import (
    MAX_QUESTION_IMAGE_BYTES,
    question_image_content_type,
)
from services.cloudinary_service import delete_file, upload_file
from services.practice_test_config import (
    ANSWER_TYPE_MCQ,
    ANSWER_TYPE_SPR,
    MODULE_FORMAT,
    SECTION_LABELS,
    SECTION_MATH,
    SECTION_READING_WRITING,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    name_modules,
    section_allows_grid_in,
)
from services.practice_test_scoring import scaled_score, spr_is_correct, total_score

router = APIRouter(tags=["practice-tests"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# --- lookups ---------------------------------------------------------------


def _get_test_or_404(db: Session, test_id: int) -> PracticeTest:
    test = (
        db.query(PracticeTest)
        .filter(PracticeTest.id == test_id, PracticeTest.deleted_at.is_(None))
        .first()
    )
    if test is None:
        raise HTTPException(status_code=404, detail="Practice test not found")
    return test


def _get_module_or_404(db: Session, module_id: int) -> PracticeTestModule:
    module = (
        db.query(PracticeTestModule)
        .filter(PracticeTestModule.id == module_id)
        .first()
    )
    if module is None:
        raise HTTPException(status_code=404, detail="Practice test module not found")
    _get_test_or_404(db, module.test_id)
    return module


def _get_question_or_404(db: Session, question_id: int) -> PracticeTestQuestion:
    question = (
        db.query(PracticeTestQuestion)
        .filter(PracticeTestQuestion.id == question_id)
        .first()
    )
    if question is None or question.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Practice test question not found")
    return question


def _modules_for(db: Session, test_ids: list[int]) -> dict[int, list[PracticeTestModule]]:
    if not test_ids:
        return {}
    rows = (
        db.query(PracticeTestModule)
        .filter(PracticeTestModule.test_id.in_(test_ids))
        .all()
    )
    grouped: dict[int, list[PracticeTestModule]] = {}
    for row in rows:
        grouped.setdefault(row.test_id, []).append(row)
    for modules in grouped.values():
        modules.sort(key=lambda m: m.order_index)
    return grouped


def _live_question_counts(db: Session, module_ids: list[int]) -> dict[int, int]:
    if not module_ids:
        return {}
    rows = (
        db.query(PracticeTestQuestion)
        .filter(
            PracticeTestQuestion.module_id.in_(module_ids),
            PracticeTestQuestion.deleted_at.is_(None),
        )
        .all()
    )
    counts: dict[int, int] = {module_id: 0 for module_id in module_ids}
    for row in rows:
        counts[row.module_id] = counts.get(row.module_id, 0) + 1
    return counts


def _class_ids_for(db: Session, test_ids: list[int]) -> dict[int, list[int]]:
    if not test_ids:
        return {}
    rows = (
        db.query(PracticeTestClass)
        .filter(PracticeTestClass.test_id.in_(test_ids))
        .all()
    )
    grouped: dict[int, list[int]] = {}
    for row in rows:
        grouped.setdefault(row.test_id, []).append(row.class_id)
    for ids in grouped.values():
        ids.sort()
    return grouped


def _student_class_ids(db: Session, student_id: int) -> list[int]:
    rows = (
        db.query(ClassEnrollment)
        .filter(ClassEnrollment.student_id == student_id)
        .all()
    )
    return [row.class_id for row in rows]


def _test_ids_for_classes(db: Session, class_ids: list[int]) -> set[int]:
    if not class_ids:
        return set()
    rows = (
        db.query(PracticeTestClass)
        .filter(PracticeTestClass.class_id.in_(class_ids))
        .all()
    )
    return {row.test_id for row in rows}


# --- serialization ---------------------------------------------------------


def _parse_choices(raw) -> list[ChoiceSchema] | None:
    if not isinstance(raw, list) or not raw:
        return None
    return [ChoiceSchema.model_validate(item) for item in raw]


def _parse_answers(raw) -> list[str] | None:
    if not isinstance(raw, list) or not raw:
        return None
    return [str(item) for item in raw]


def _resolved_image_scale(question: PracticeTestQuestion) -> float:
    value = getattr(question, "image_scale", None)
    if value is None:
        return 0.85
    return min(1.0, max(0.4, float(value)))


def serialize_question_admin(question: PracticeTestQuestion) -> dict:
    payload = PracticeTestQuestionAdminSchema(
        id=question.id,
        module_id=question.module_id,
        order_index=question.order_index,
        domain=question.domain,
        difficulty=question.difficulty,
        passage_text=question.passage_text,
        question_text=question.question_text,
        explanation=question.explanation,
        question_image=question.question_image,
        question_image_public_id=question.question_image_public_id,
        image_scale=_resolved_image_scale(question),
        answer_type=question.answer_type,
        choices=_parse_choices(question.choices),
        correct_choice=question.correct_choice,
        correct_answers=_parse_answers(question.correct_answers),
        created_at=question.created_at,
        created_by_id=question.created_by_id,
    )
    return payload.model_dump(mode="json")


def _serialize_tests(db: Session, tests: list[PracticeTest]) -> list[dict]:
    test_ids = [test.id for test in tests]
    modules_by_test = _modules_for(db, test_ids)
    module_ids = [m.id for modules in modules_by_test.values() for m in modules]
    counts = _live_question_counts(db, module_ids)
    classes_by_test = _class_ids_for(db, test_ids)

    payloads = []
    for test in tests:
        modules = modules_by_test.get(test.id, [])
        names = name_modules([module.section for module in modules])
        module_payloads = [
            PracticeTestModuleSchema(
                id=module.id,
                name=name,
                section=module.section,
                order_index=module.order_index,
                time_limit_seconds=module.time_limit_seconds,
                required_question_count=module.required_question_count,
                question_count=counts.get(module.id, 0),
            )
            for module, name in zip(modules, names)
        ]
        publishable = bool(module_payloads) and all(
            item.question_count == item.required_question_count
            for item in module_payloads
        )
        payloads.append(
            PracticeTestSchema(
                id=test.id,
                title=test.title,
                description=test.description,
                visible=test.visible,
                created_at=test.created_at,
                created_by_id=test.created_by_id,
                class_ids=classes_by_test.get(test.id, []),
                modules=module_payloads,
                is_publishable=publishable,
            ).model_dump(mode="json")
        )
    return payloads


def _serialize_test(db: Session, test: PracticeTest) -> dict:
    return _serialize_tests(db, [test])[0]


# --- authoring validation --------------------------------------------------


def _order_index_taken(
    db: Session,
    module_id: int,
    order_index: int,
    *,
    exclude_id: int | None = None,
) -> bool:
    existing = (
        db.query(PracticeTestQuestion)
        .filter(
            PracticeTestQuestion.module_id == module_id,
            PracticeTestQuestion.order_index == order_index,
            PracticeTestQuestion.deleted_at.is_(None),
        )
        .first()
    )
    if existing is None:
        return False
    return exclude_id is None or existing.id != exclude_id


def _validate_against_module(
    module: PracticeTestModule,
    data: PracticeTestQuestionCreate,
) -> None:
    if data.order_index > module.required_question_count:
        raise HTTPException(
            status_code=422,
            detail=(
                f"order_index must be between 1 and "
                f"{module.required_question_count} for this module"
            ),
        )
    if data.answer_type == ANSWER_TYPE_SPR and not section_allows_grid_in(module.section):
        raise HTTPException(
            status_code=422,
            detail="Grid-in questions are only allowed in the math module",
        )


def _apply_question_payload(
    question: PracticeTestQuestion,
    data: PracticeTestQuestionCreate,
) -> PracticeTestQuestion:
    question.order_index = data.order_index
    question.domain = data.domain
    question.difficulty = data.difficulty
    question.passage_text = data.passage_text
    question.question_text = data.question_text
    question.explanation = data.explanation
    question.question_image = data.question_image
    question.question_image_public_id = data.question_image_public_id
    question.image_scale = data.image_scale
    question.answer_type = data.answer_type
    question.choices = (
        [choice.model_dump() for choice in data.choices] if data.choices else None
    )
    question.correct_choice = data.correct_choice
    question.correct_answers = list(data.correct_answers) if data.correct_answers else None
    return question


def _first_incomplete_module(
    db: Session, test: PracticeTest
) -> tuple[PracticeTestModule, int, str] | None:
    """The module blocking publication, its live count, and its display name.

    The name matters: a test has two Reading & Writing modules, so "the
    reading_writing module is short" would not say which one to open.
    """
    modules = _modules_for(db, [test.id]).get(test.id, [])
    if not modules:
        return None
    counts = _live_question_counts(db, [module.id for module in modules])
    names = name_modules([module.section for module in modules])
    for module, name in zip(modules, names):
        count = counts.get(module.id, 0)
        if count != module.required_question_count:
            return module, count, name
    return None


# --- questions -------------------------------------------------------------
# Declared before the /practice-tests/{test_id} routes so the literal prefixes win.


@router.post("/practice-tests/questions/image")
async def upload_practice_question_image(
    file: UploadFile = File(...),
    current_user: AuthUser = Depends(require_staff),
):
    filename = file.filename or "question.png"
    content_type = question_image_content_type(filename, file.content_type)
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
        folder="practice_test_questions",
        public_id_prefix="practice",
    )
    return {"url": uploaded["url"], "public_id": uploaded["public_id"]}


@router.post("/practice-tests/modules/{module_id}/questions")
def create_practice_question(
    module_id: int,
    data: PracticeTestQuestionCreate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    module = _get_module_or_404(db, module_id)
    _validate_against_module(module, data)
    if _order_index_taken(db, module_id, data.order_index):
        raise HTTPException(
            status_code=409,
            detail=f"This module already has a question at position {data.order_index}",
        )

    question = _apply_question_payload(PracticeTestQuestion(), data)
    question.module_id = module_id
    question.created_at = _utcnow()
    question.created_by_id = current_user.id
    db.add(question)
    db.commit()
    db.refresh(question)
    return serialize_question_admin(question)


@router.get("/practice-tests/modules/{module_id}/questions")
def list_practice_questions(
    module_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    _get_module_or_404(db, module_id)
    questions = (
        db.query(PracticeTestQuestion)
        .filter(
            PracticeTestQuestion.module_id == module_id,
            PracticeTestQuestion.deleted_at.is_(None),
        )
        .all()
    )
    questions.sort(key=lambda q: q.order_index)
    return [serialize_question_admin(question) for question in questions]


@router.put("/practice-tests/questions/{question_id}")
def update_practice_question(
    question_id: int,
    data: PracticeTestQuestionUpdate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    question = _get_question_or_404(db, question_id)
    module = _get_module_or_404(db, question.module_id)
    _validate_against_module(module, data)
    if _order_index_taken(db, module.id, data.order_index, exclude_id=question.id):
        raise HTTPException(
            status_code=409,
            detail=f"This module already has a question at position {data.order_index}",
        )

    old_public_id = question.question_image_public_id
    _apply_question_payload(question, data)
    db.commit()
    db.refresh(question)
    if old_public_id and old_public_id != question.question_image_public_id:
        delete_file(old_public_id, "image/jpeg")
    return serialize_question_admin(question)


@router.delete("/practice-tests/questions/{question_id}")
def delete_practice_question(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    question = _get_question_or_404(db, question_id)
    module = _get_module_or_404(db, question.module_id)
    question.deleted_at = _utcnow()
    db.commit()

    # A published test that just lost a question would hand students a short
    # module, so it drops back to draft until the slot is filled again.
    test = _get_test_or_404(db, module.test_id)
    unpublished = False
    if test.visible and _first_incomplete_module(db, test) is not None:
        test.visible = False
        unpublished = True
        db.commit()

    return {"ok": True, "id": question_id, "test_unpublished": unpublished}


# --- tests -----------------------------------------------------------------


@router.post("/practice-tests")
def create_practice_test(
    data: PracticeTestCreate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    test = PracticeTest(
        title=data.title,
        description=data.description,
        visible=False,
        created_at=_utcnow(),
        created_by_id=current_user.id,
    )
    db.add(test)
    db.commit()
    db.refresh(test)

    for spec in MODULE_FORMAT:
        db.add(
            PracticeTestModule(
                test_id=test.id,
                section=spec.section,
                order_index=spec.order_index,
                time_limit_seconds=spec.time_limit_seconds,
                required_question_count=spec.required_question_count,
            )
        )
    db.commit()
    return _serialize_test(db, test)


@router.get("/practice-tests")
def list_practice_tests(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    query = db.query(PracticeTest).filter(PracticeTest.deleted_at.is_(None))

    if current_user.role == "student":
        allowed = _test_ids_for_classes(db, _student_class_ids(db, current_user.id))
        if not allowed:
            return []
        tests = query.filter(
            PracticeTest.visible.is_(True),
            PracticeTest.id.in_(sorted(allowed)),
        ).all()
    else:
        tests = query.all()

    tests.sort(key=lambda t: t.id, reverse=True)
    return _serialize_tests(db, tests)


@router.get("/practice-tests/{test_id}")
def get_practice_test(
    test_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    test = _get_test_or_404(db, test_id)
    if current_user.role == "student":
        allowed = _test_ids_for_classes(db, _student_class_ids(db, current_user.id))
        if not test.visible or test.id not in allowed:
            raise HTTPException(status_code=404, detail="Practice test not found")
    return _serialize_test(db, test)


@router.patch("/practice-tests/{test_id}")
def update_practice_test(
    test_id: int,
    data: PracticeTestUpdate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    test = _get_test_or_404(db, test_id)
    if data.title is not None:
        test.title = data.title
    if data.description is not None:
        test.description = data.description
    db.commit()
    db.refresh(test)
    return _serialize_test(db, test)


@router.delete("/practice-tests/{test_id}")
def delete_practice_test(
    test_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    test = _get_test_or_404(db, test_id)
    test.deleted_at = _utcnow()
    test.visible = False
    db.commit()
    return {"ok": True, "id": test_id}


@router.put("/practice-tests/{test_id}/classes")
def set_practice_test_classes(
    test_id: int,
    data: PracticeTestClassesUpdate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    test = _get_test_or_404(db, test_id)

    # A teacher can only publish to classes they own; admins and mentors see all.
    allowed_ids = {row.id for row in classes_query(db, current_user).all()}
    unknown = [cid for cid in data.class_ids if cid not in allowed_ids]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Not allowed to assign this test to class(es): {unknown}",
        )

    existing = (
        db.query(PracticeTestClass)
        .filter(PracticeTestClass.test_id == test_id)
        .all()
    )
    for row in existing:
        db.delete(row)
    for class_id in data.class_ids:
        db.add(PracticeTestClass(test_id=test_id, class_id=class_id))
    db.commit()
    return _serialize_test(db, test)


@router.patch("/practice-tests/{test_id}/visible")
def set_practice_test_visibility(
    test_id: int,
    data: PracticeTestVisibilityUpdate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    test = _get_test_or_404(db, test_id)

    if data.visible:
        incomplete = _first_incomplete_module(db, test)
        if incomplete is None and not _modules_for(db, [test.id]).get(test.id):
            raise HTTPException(
                status_code=409,
                detail="This test has no modules and cannot be published",
            )
        if incomplete is not None:
            module, count, name = incomplete
            raise HTTPException(
                status_code=409,
                detail=(
                    f"The {name} module has {count} of "
                    f"{module.required_question_count} questions"
                ),
            )

    test.visible = data.visible
    db.commit()
    db.refresh(test)
    return _serialize_test(db, test)


# ===========================================================================
# Taking + review
# ===========================================================================


def _get_attempt_or_404(db: Session, attempt_id: int) -> PracticeTestAttempt:
    attempt = (
        db.query(PracticeTestAttempt)
        .filter(PracticeTestAttempt.id == attempt_id)
        .first()
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Practice test attempt not found")
    return attempt


def _require_owner(attempt: PracticeTestAttempt, user: AuthUser, action: str) -> None:
    if attempt.student_id != user.id:
        raise HTTPException(
            status_code=403,
            detail=f"Only the attempt owner can {action}",
        )


def _require_in_progress(attempt: PracticeTestAttempt) -> None:
    if attempt.status != STATUS_IN_PROGRESS:
        raise HTTPException(
            status_code=409,
            detail="This practice test attempt is no longer in progress",
        )


def _teacher_can_view_student(db: Session, user: AuthUser, student_id: int) -> bool:
    enrollments = (
        db.query(ClassEnrollment)
        .filter(ClassEnrollment.student_id == student_id)
        .all()
    )
    class_ids = [enrollment.class_id for enrollment in enrollments]
    if not class_ids:
        return False
    classes = classes_query(db, user).filter(Class.id.in_(class_ids)).all()
    return any(teacher_owns_class(user, class_obj) for class_obj in classes)


def _require_review_access(
    db: Session, attempt: PracticeTestAttempt, user: AuthUser
) -> None:
    if attempt.student_id == user.id or is_admin_or_mentor(user.role):
        return
    if normalize_role(user.role) == "teacher" and _teacher_can_view_student(
        db, user, attempt.student_id
    ):
        return
    raise HTTPException(status_code=403, detail="Not enough permissions")


def _parse_question_ids(raw) -> list[int]:
    if not isinstance(raw, list):
        return []
    return [int(item) for item in raw if isinstance(item, (int, str)) and str(item).isdigit()]


def _ordered_test_questions(db: Session, test_id: int) -> list[PracticeTestQuestion]:
    """Live questions for a test in presentation order: module, then position."""
    modules = _modules_for(db, [test_id]).get(test_id, [])
    ordered: list[PracticeTestQuestion] = []
    for module in modules:
        questions = (
            db.query(PracticeTestQuestion)
            .filter(
                PracticeTestQuestion.module_id == module.id,
                PracticeTestQuestion.deleted_at.is_(None),
            )
            .all()
        )
        questions.sort(key=lambda q: q.order_index)
        ordered.extend(questions)
    return ordered


def _questions_for_attempt(
    db: Session, attempt: PracticeTestAttempt
) -> list[PracticeTestQuestion]:
    """The attempt's frozen question set, soft-deleted rows included."""
    ids = _parse_question_ids(attempt.question_ids)
    if not ids:
        return _ordered_test_questions(db, attempt.test_id)
    rows = (
        db.query(PracticeTestQuestion)
        .filter(PracticeTestQuestion.id.in_(ids))
        .all()
    )
    by_id = {row.id: row for row in rows}
    return [by_id[qid] for qid in ids if qid in by_id]


def _sections_by_module(db: Session, test_id: int) -> dict[int, str]:
    return {
        module.id: module.section
        for module in _modules_for(db, [test_id]).get(test_id, [])
    }


def serialize_question_public(question: PracticeTestQuestion, section: str) -> dict:
    payload = PracticeTestQuestionPublicSchema(
        id=question.id,
        module_id=question.module_id,
        section=section,
        order_index=question.order_index,
        domain=question.domain,
        passage_text=question.passage_text,
        question_text=question.question_text,
        question_image=question.question_image,
        image_scale=_resolved_image_scale(question),
        answer_type=question.answer_type,
        choices=_parse_choices(question.choices),
    )
    return payload.model_dump(mode="json")


def _serialize_answers(
    answers: list[PracticeTestAnswer], *, include_correctness: bool
) -> list[PracticeTestAnswerSchema]:
    return [
        PracticeTestAnswerSchema(
            question_id=answer.question_id,
            selected_choice=answer.selected_choice,
            response_text=answer.response_text,
            answered_at=answer.answered_at,
            is_correct=answer.is_correct if include_correctness else None,
        )
        for answer in answers
    ]


def _serialize_attempt(
    attempt: PracticeTestAttempt,
    *,
    test_title: str | None = None,
    answers: list[PracticeTestAnswer] | None = None,
    include_correctness: bool = False,
) -> PracticeTestAttemptSchema:
    return PracticeTestAttemptSchema(
        id=attempt.id,
        test_id=attempt.test_id,
        test_title=test_title,
        student_id=attempt.student_id,
        status=attempt.status,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        current_module_id=attempt.current_module_id,
        current_question_id=attempt.current_question_id,
        module_started_at=attempt.module_started_at,
        timer_paused_at=getattr(attempt, "timer_paused_at", None),
        timer_pause_seconds=int(getattr(attempt, "timer_pause_seconds", 0) or 0),
        rw_raw=attempt.rw_raw,
        math_raw=attempt.math_raw,
        rw_scaled=attempt.rw_scaled,
        math_scaled=attempt.math_scaled,
        total_scaled=attempt.total_scaled,
        answers=_serialize_answers(
            answers or [], include_correctness=include_correctness
        ),
    )


def _answers_for(db: Session, attempt_id: int) -> list[PracticeTestAnswer]:
    return (
        db.query(PracticeTestAnswer)
        .filter(PracticeTestAnswer.attempt_id == attempt_id)
        .all()
    )


def _titles_for(db: Session, test_ids: list[int]) -> dict[int, str]:
    if not test_ids:
        return {}
    rows = db.query(PracticeTest).filter(PracticeTest.id.in_(test_ids)).all()
    return {row.id: row.title for row in rows}


def _grade(question: PracticeTestQuestion, data: PracticeTestAnswerSubmit) -> bool:
    if question.answer_type == ANSWER_TYPE_MCQ:
        expected = (question.correct_choice or "").strip().upper()
        return bool(data.selected_choice) and data.selected_choice == expected
    return spr_is_correct(data.response_text or "", _parse_answers(question.correct_answers) or [])


# --- attempts ---------------------------------------------------------------


@router.get("/practice-tests/attempts/me")
def list_my_practice_attempts(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempts = (
        db.query(PracticeTestAttempt)
        .filter(PracticeTestAttempt.student_id == current_user.id)
        .all()
    )
    attempts.sort(key=lambda a: a.id, reverse=True)
    titles = _titles_for(db, [attempt.test_id for attempt in attempts])
    return [
        _serialize_attempt(
            attempt, test_title=titles.get(attempt.test_id)
        ).model_dump(mode="json")
        for attempt in attempts
    ]


@router.get("/practice-tests/attempts/{attempt_id}/questions")
def get_practice_attempt_questions(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = _get_attempt_or_404(db, attempt_id)
    if attempt.student_id != current_user.id and not is_admin_or_mentor(current_user.role):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    sections = _sections_by_module(db, attempt.test_id)
    return [
        serialize_question_public(question, sections.get(question.module_id, ""))
        for question in _questions_for_attempt(db, attempt)
    ]


@router.post("/practice-tests/attempts/{attempt_id}/answers")
def submit_practice_answer(
    attempt_id: int,
    data: PracticeTestAnswerSubmit,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = _get_attempt_or_404(db, attempt_id)
    _require_owner(attempt, current_user, "submit answers")
    _require_in_progress(attempt)

    frozen = {question.id: question for question in _questions_for_attempt(db, attempt)}
    question = frozen.get(data.question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Practice test question not found")

    if question.answer_type == ANSWER_TYPE_MCQ and data.response_text is not None:
        raise HTTPException(
            status_code=422, detail="This question expects a multiple-choice answer"
        )
    if question.answer_type == ANSWER_TYPE_SPR and data.selected_choice is not None:
        raise HTTPException(
            status_code=422, detail="This question expects a typed answer"
        )

    answer = (
        db.query(PracticeTestAnswer)
        .filter(
            PracticeTestAnswer.attempt_id == attempt_id,
            PracticeTestAnswer.question_id == data.question_id,
        )
        .first()
    )
    if answer is None:
        answer = PracticeTestAnswer(attempt_id=attempt_id, question_id=data.question_id)
        db.add(answer)

    answer.selected_choice = data.selected_choice
    answer.response_text = data.response_text
    answer.is_correct = _grade(question, data)
    answer.answered_at = _utcnow()
    db.commit()

    # Correctness is deliberately withheld until the attempt is completed.
    return {
        "question_id": answer.question_id,
        "selected_choice": answer.selected_choice,
        "response_text": answer.response_text,
        "answered_at": answer.answered_at,
    }


@router.patch("/practice-tests/attempts/{attempt_id}/progress")
def save_practice_progress(
    attempt_id: int,
    data: PracticeTestProgressUpdate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = _get_attempt_or_404(db, attempt_id)
    _require_owner(attempt, current_user, "save progress")
    _require_in_progress(attempt)

    modules = {m.id: m for m in _modules_for(db, [attempt.test_id]).get(attempt.test_id, [])}

    if data.current_module_id is not None and data.current_module_id != attempt.current_module_id:
        target = modules.get(data.current_module_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Practice test module not found")
        current = modules.get(attempt.current_module_id)
        if current is not None and target.order_index < current.order_index:
            raise HTTPException(
                status_code=409,
                detail="Modules cannot be revisited once you have moved on",
            )
        attempt.current_module_id = target.id
        attempt.module_started_at = _utcnow()
        attempt.timer_paused_at = None
        attempt.timer_pause_seconds = 0

    if data.current_question_id is not None:
        frozen = {q.id for q in _questions_for_attempt(db, attempt)}
        if data.current_question_id not in frozen:
            raise HTTPException(
                status_code=404, detail="Practice test question not found"
            )
        attempt.current_question_id = data.current_question_id

    if data.pause_timer is True:
        if getattr(attempt, "timer_paused_at", None) is None:
            attempt.timer_paused_at = _utcnow()
    elif data.pause_timer is False:
        paused_at = getattr(attempt, "timer_paused_at", None)
        if paused_at is not None:
            extra = int((_utcnow() - _as_utc(paused_at)).total_seconds())
            attempt.timer_pause_seconds = int(
                getattr(attempt, "timer_pause_seconds", 0) or 0
            ) + max(0, extra)
            attempt.timer_paused_at = None

    db.commit()
    db.refresh(attempt)
    return {
        "current_module_id": attempt.current_module_id,
        "current_question_id": attempt.current_question_id,
        "module_started_at": attempt.module_started_at,
        "timer_paused_at": attempt.timer_paused_at,
        "timer_pause_seconds": int(getattr(attempt, "timer_pause_seconds", 0) or 0),
    }


@router.post("/practice-tests/attempts/{attempt_id}/complete")
def complete_practice_attempt(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = _get_attempt_or_404(db, attempt_id)
    _require_owner(attempt, current_user, "complete this attempt")
    if attempt.status == STATUS_COMPLETED:
        raise HTTPException(
            status_code=409, detail="This practice test attempt is already completed"
        )
    _require_in_progress(attempt)

    questions = _questions_for_attempt(db, attempt)
    sections = _sections_by_module(db, attempt.test_id)
    correct = {
        answer.question_id: bool(answer.is_correct)
        for answer in _answers_for(db, attempt_id)
    }

    totals = {SECTION_READING_WRITING: [0, 0], SECTION_MATH: [0, 0]}
    for question in questions:
        section = sections.get(question.module_id)
        if section not in totals:
            continue
        totals[section][1] += 1
        if correct.get(question.id):
            totals[section][0] += 1

    rw_raw, rw_max = totals[SECTION_READING_WRITING]
    math_raw, math_max = totals[SECTION_MATH]

    attempt.rw_raw = rw_raw
    attempt.math_raw = math_raw
    attempt.rw_scaled = scaled_score(rw_raw, rw_max)
    attempt.math_scaled = scaled_score(math_raw, math_max)
    attempt.total_scaled = total_score(attempt.rw_scaled, attempt.math_scaled)
    attempt.status = STATUS_COMPLETED
    attempt.completed_at = _utcnow()
    attempt.timer_paused_at = None
    db.commit()
    db.refresh(attempt)

    titles = _titles_for(db, [attempt.test_id])
    return _serialize_attempt(
        attempt,
        test_title=titles.get(attempt.test_id),
        answers=_answers_for(db, attempt_id),
        include_correctness=True,
    ).model_dump(mode="json")


@router.get("/practice-tests/attempts/{attempt_id}/detail")
def get_practice_attempt_detail(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    attempt = _get_attempt_or_404(db, attempt_id)
    _require_review_access(db, attempt, current_user)
    if attempt.status != STATUS_COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Review is only available for completed attempts",
        )

    student = db.query(User).filter(User.id == attempt.student_id).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")

    answers = {answer.question_id: answer for answer in _answers_for(db, attempt_id)}
    sections = _sections_by_module(db, attempt.test_id)
    items = []
    for question in _questions_for_attempt(db, attempt):
        answer = answers.get(question.id)
        items.append(
            PracticeTestReviewItem(
                section=sections.get(question.module_id, ""),
                question=PracticeTestQuestionAdminSchema(
                    **serialize_question_admin(question)
                ),
                selected_choice=answer.selected_choice if answer else None,
                response_text=answer.response_text if answer else None,
                is_correct=bool(answer.is_correct) if answer else False,
            )
        )

    titles = _titles_for(db, [attempt.test_id])
    payload = PracticeTestAttemptDetail(
        attempt=_serialize_attempt(
            attempt,
            test_title=titles.get(attempt.test_id),
            answers=list(answers.values()),
            include_correctness=True,
        ),
        student=PracticeTestStudentSummary(
            id=student.id, name=student.name, surname=student.surname
        ),
        items=items,
    )
    return payload.model_dump(mode="json")


@router.post("/practice-tests/{test_id}/attempts")
def create_practice_attempt(
    test_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    test = _get_test_or_404(db, test_id)

    allowed = _test_ids_for_classes(db, _student_class_ids(db, current_user.id))
    if not test.visible or test.id not in allowed:
        raise HTTPException(
            status_code=403, detail="This practice test is not available to you"
        )

    existing = (
        db.query(PracticeTestAttempt)
        .filter(
            PracticeTestAttempt.test_id == test_id,
            PracticeTestAttempt.student_id == current_user.id,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="You have already taken this practice test",
        )

    questions = _ordered_test_questions(db, test_id)
    if not questions:
        raise HTTPException(
            status_code=409, detail="This practice test has no questions"
        )

    modules = _modules_for(db, [test_id]).get(test_id, [])
    attempt = PracticeTestAttempt(
        test_id=test_id,
        student_id=current_user.id,
        status=STATUS_IN_PROGRESS,
        started_at=_utcnow(),
        current_module_id=modules[0].id if modules else None,
        current_question_id=questions[0].id,
        module_started_at=_utcnow(),
        timer_pause_seconds=0,
        question_ids=[question.id for question in questions],
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    return PracticeTestAttemptCreated(
        attempt_id=attempt.id,
        test_id=test_id,
        status=attempt.status,
        started_at=attempt.started_at,
        current_module_id=attempt.current_module_id,
    ).model_dump(mode="json")


@router.get("/practice-tests/{test_id}/attempts")
def list_practice_attempts_for_test(
    test_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_staff),
):
    _get_test_or_404(db, test_id)
    attempts = (
        db.query(PracticeTestAttempt)
        .filter(
            PracticeTestAttempt.test_id == test_id,
            PracticeTestAttempt.status == STATUS_COMPLETED,
        )
        .all()
    )
    if not is_admin_or_mentor(current_user.role):
        attempts = [
            attempt
            for attempt in attempts
            if _teacher_can_view_student(db, current_user, attempt.student_id)
        ]

    students = {}
    if attempts:
        rows = (
            db.query(User)
            .filter(User.id.in_([attempt.student_id for attempt in attempts]))
            .all()
        )
        students = {row.id: row for row in rows}

    attempts.sort(key=lambda a: a.total_scaled or 0, reverse=True)
    payloads = []
    for attempt in attempts:
        student = students.get(attempt.student_id)
        item = _serialize_attempt(attempt).model_dump(mode="json")
        item["student"] = (
            PracticeTestStudentSummary(
                id=student.id, name=student.name, surname=student.surname
            ).model_dump(mode="json")
            if student
            else None
        )
        payloads.append(item)
    return payloads
