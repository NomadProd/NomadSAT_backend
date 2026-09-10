from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.diagnostic import ChoiceSchema
from services.practice_test_config import (
    ANSWER_TYPE_MCQ,
    ANSWER_TYPE_SPR,
    ANSWER_TYPES,
    DIFFICULTIES,
)
from services.practice_test_scoring import is_valid_grid_in

__all__ = [
    "ChoiceSchema",
    "PracticeTestClassesUpdate",
    "PracticeTestCreate",
    "PracticeTestModuleSchema",
    "PracticeTestQuestionAdminSchema",
    "PracticeTestQuestionCreate",
    "PracticeTestQuestionUpdate",
    "PracticeTestSchema",
    "PracticeTestUpdate",
    "PracticeTestVisibilityUpdate",
]


def _stripped_required(value: str, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


class PracticeTestCreate(BaseModel):
    title: str
    description: Optional[str] = None

    @field_validator("title")
    @classmethod
    def require_title(cls, value: str) -> str:
        return _stripped_required(value, "title")

    @field_validator("description")
    @classmethod
    def strip_optional(cls, value: Optional[str]) -> Optional[str]:
        return (value or "").strip() or None if value is not None else None


class PracticeTestUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None

    @field_validator("title")
    @classmethod
    def require_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _stripped_required(value, "title")

    @field_validator("description")
    @classmethod
    def strip_optional(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip() or None


class PracticeTestClassesUpdate(BaseModel):
    class_ids: list[int]

    @field_validator("class_ids")
    @classmethod
    def dedupe(cls, value: list[int]) -> list[int]:
        return sorted(set(value))


class PracticeTestVisibilityUpdate(BaseModel):
    visible: bool


class PracticeTestQuestionCreate(BaseModel):
    order_index: int = Field(ge=1)
    domain: str
    difficulty: str
    passage_text: Optional[str] = None
    question_text: str
    explanation: Optional[str] = None
    question_image: Optional[str] = None
    question_image_public_id: Optional[str] = None
    image_scale: float = Field(default=0.85, ge=0.4, le=1.0)
    answer_type: str
    choices: Optional[list[ChoiceSchema]] = None
    correct_choice: Optional[str] = None
    correct_answers: Optional[list[str]] = None

    @field_validator("difficulty", "answer_type")
    @classmethod
    def normalize_lower(cls, value: str) -> str:
        return (value or "").strip().lower()

    @field_validator("domain", "question_text")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return _stripped_required(value, "this field")

    @field_validator(
        "passage_text",
        "explanation",
        "question_image",
        "question_image_public_id",
    )
    @classmethod
    def strip_optional(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("correct_choice")
    @classmethod
    def normalize_correct_choice(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip().upper() or None

    @field_validator("correct_answers")
    @classmethod
    def strip_answers(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        return [str(item).strip() for item in value if str(item).strip()]

    @model_validator(mode="after")
    def validate_answer_shape(self) -> "PracticeTestQuestionCreate":
        if self.difficulty not in DIFFICULTIES:
            raise ValueError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
        if self.answer_type not in ANSWER_TYPES:
            raise ValueError(f"answer_type must be one of {', '.join(ANSWER_TYPES)}")

        if self.answer_type == ANSWER_TYPE_MCQ:
            if self.correct_answers:
                raise ValueError("correct_answers is only for grid-in questions")
            if not self.choices or len(self.choices) < 2:
                raise ValueError("at least two choices are required")
            keys = [choice.key for choice in self.choices]
            if len(set(keys)) != len(keys):
                raise ValueError("choice keys must be unique")
            if not self.correct_choice:
                raise ValueError("correct_choice is required")
            if self.correct_choice not in set(keys):
                raise ValueError("correct_choice must match one of the choice keys")
            self.correct_answers = None
        else:
            if self.choices or self.correct_choice:
                raise ValueError("grid-in questions cannot have choices")
            if not self.correct_answers:
                raise ValueError("at least one correct answer is required")
            unusable = [a for a in self.correct_answers if not is_valid_grid_in(a)]
            if unusable:
                raise ValueError(
                    "these answers cannot be typed into the grid-in field: "
                    + ", ".join(unusable)
                )
            self.choices = None
            self.correct_choice = None
        return self


class PracticeTestQuestionUpdate(PracticeTestQuestionCreate):
    pass


class PracticeTestQuestionAdminSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    module_id: int
    order_index: int
    domain: str
    difficulty: str
    passage_text: Optional[str] = None
    question_text: str
    explanation: Optional[str] = None
    question_image: Optional[str] = None
    question_image_public_id: Optional[str] = None
    image_scale: float = 0.85
    answer_type: str
    choices: Optional[list[ChoiceSchema]] = None
    correct_choice: Optional[str] = None
    correct_answers: Optional[list[str]] = None
    created_at: Optional[datetime] = None
    created_by_id: Optional[int] = None


class PracticeTestModuleSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    section: str
    order_index: int
    time_limit_seconds: int
    required_question_count: int
    question_count: int


class PracticeTestSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = None
    visible: bool
    created_at: Optional[datetime] = None
    created_by_id: Optional[int] = None
    class_ids: list[int] = []
    modules: list[PracticeTestModuleSchema] = []
    is_publishable: bool = False


# --- taking / review --------------------------------------------------------


class PracticeTestAttemptCreated(BaseModel):
    attempt_id: int
    test_id: int
    status: str
    started_at: Optional[datetime] = None
    current_module_id: Optional[int] = None


class PracticeTestAnswerSubmit(BaseModel):
    question_id: int
    selected_choice: Optional[str] = None
    response_text: Optional[str] = None

    @field_validator("selected_choice")
    @classmethod
    def normalize_choice(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip().upper() or None

    @field_validator("response_text")
    @classmethod
    def strip_response(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip() or None


class PracticeTestProgressUpdate(BaseModel):
    current_question_id: Optional[int] = None
    current_module_id: Optional[int] = None
    pause_timer: Optional[bool] = None


class PracticeTestQuestionPublicSchema(BaseModel):
    """What a student may see while taking. Never carries the answer key."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    module_id: int
    section: str
    order_index: int
    domain: str
    passage_text: Optional[str] = None
    question_text: str
    question_image: Optional[str] = None
    image_scale: float = 0.85
    answer_type: str
    choices: Optional[list[ChoiceSchema]] = None


class PracticeTestAnswerSchema(BaseModel):
    question_id: int
    selected_choice: Optional[str] = None
    response_text: Optional[str] = None
    answered_at: Optional[datetime] = None
    is_correct: Optional[bool] = None


class PracticeTestAttemptSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    test_id: int
    test_title: Optional[str] = None
    student_id: int
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    current_module_id: Optional[int] = None
    current_question_id: Optional[int] = None
    module_started_at: Optional[datetime] = None
    timer_paused_at: Optional[datetime] = None
    timer_pause_seconds: int = 0
    rw_raw: Optional[int] = None
    math_raw: Optional[int] = None
    rw_scaled: Optional[int] = None
    math_scaled: Optional[int] = None
    total_scaled: Optional[int] = None
    answers: list[PracticeTestAnswerSchema] = []


class PracticeTestStudentSummary(BaseModel):
    id: int
    name: str
    surname: str


class PracticeTestReviewItem(BaseModel):
    section: str
    question: PracticeTestQuestionAdminSchema
    selected_choice: Optional[str] = None
    response_text: Optional[str] = None
    is_correct: Optional[bool] = None


class PracticeTestAttemptDetail(BaseModel):
    attempt: PracticeTestAttemptSchema
    student: PracticeTestStudentSummary
    items: list[PracticeTestReviewItem] = []
