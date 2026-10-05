from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.diagnostic_config import SECTION_MATH, layout_mismatch_message
from services.practice_test_config import ANSWER_TYPE_MCQ, ANSWER_TYPES
from services.practice_test_scoring import is_valid_grid_in


class ChoiceSchema(BaseModel):
    key: str
    text: str

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        key = (value or "").strip().upper()
        if not key:
            raise ValueError("choice key is required")
        return key

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("choice text is required")
        return text


def _validate_layout(
    order_index: int,
    section: str,
    domain: str,
    difficulty: str,
    points: int,
) -> None:
    message = layout_mismatch_message(
        order_index=order_index,
        section=section,
        domain=domain,
        difficulty=difficulty,
        points=points,
    )
    if message:
        raise ValueError(message)


class DiagnosticQuestionCreate(BaseModel):
    section: str
    domain: str
    difficulty: str
    points: int
    order_index: int
    passage_text: Optional[str] = None
    question_text: str
    question_url: Optional[str] = None
    question_image: Optional[str] = None
    question_image_public_id: Optional[str] = None
    image_scale: float = Field(default=0.85, ge=0.4, le=1.0)
    answer_type: str = ANSWER_TYPE_MCQ
    choices: Optional[list[ChoiceSchema]] = None
    correct_choice: Optional[str] = None
    correct_answers: Optional[list[str]] = None
    explanation: Optional[str] = None

    @field_validator("section", "difficulty", "answer_type")
    @classmethod
    def normalize_lower(cls, value: str) -> str:
        return (value or "").strip().lower()

    @field_validator("domain", "question_text")
    @classmethod
    def strip_required(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("this field is required")
        return text

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

    @field_validator("question_url", "question_image", "question_image_public_id", "passage_text", "explanation")
    @classmethod
    def strip_optional(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @model_validator(mode="after")
    def validate_layout_and_answer(self) -> "DiagnosticQuestionCreate":
        _validate_layout(
            order_index=self.order_index,
            section=self.section,
            domain=self.domain,
            difficulty=self.difficulty,
            points=self.points,
        )
        if self.answer_type not in ANSWER_TYPES:
            raise ValueError(f"answer_type must be one of {', '.join(ANSWER_TYPES)}")

        # Same answer-shape rules as PracticeTestQuestionCreate.
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
            if self.section != SECTION_MATH:
                raise ValueError("grid-in questions are only allowed in the math section")
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


class DiagnosticQuestionUpdate(DiagnosticQuestionCreate):
    pass


class DiagnosticQuestionAdminSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    section: str
    domain: str
    difficulty: str
    points: int
    order_index: int
    passage_text: Optional[str] = None
    question_text: str
    question_url: Optional[str] = None
    question_image: Optional[str] = None
    question_image_public_id: Optional[str] = None
    image_scale: float = 0.85
    answer_type: str = ANSWER_TYPE_MCQ
    choices: list[ChoiceSchema]
    correct_choice: Optional[str] = None
    correct_answers: Optional[list[str]] = None
    explanation: Optional[str] = None
    created_at: datetime
    created_by_id: Optional[int] = None


class DiagnosticQuestionPublicSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    section: str
    domain: str
    difficulty: str
    order_index: int
    passage_text: Optional[str] = None
    question_text: str
    question_image: Optional[str] = None
    image_scale: float = 0.85
    answer_type: str = ANSWER_TYPE_MCQ
    choices: list[ChoiceSchema]


class DiagnosticAnswerSubmit(BaseModel):
    question_id: int
    selected_choice: Optional[str] = None
    # Grid-in input. Empty or blank clears the answer.
    response_text: Optional[str] = None
    is_correct: Optional[bool] = Field(
        default=None,
        description="Ignored if present. is_correct is always computed server-side.",
    )

    @field_validator("selected_choice")
    @classmethod
    def normalize_selected_choice(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip().upper() or None

    @field_validator("response_text")
    @classmethod
    def strip_response(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip()


class DiagnosticAnswerSchema(BaseModel):
    question_id: int
    selected_choice: Optional[str] = None
    response_text: Optional[str] = None
    answered_at: Optional[datetime] = None
    is_correct: Optional[bool] = None


class DiagnosticAttemptSchema(BaseModel):
    id: int
    student_id: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str
    rw_points: Optional[int] = None
    math_points: Optional[int] = None
    rw_scaled_estimate: Optional[int] = None
    math_scaled_estimate: Optional[int] = None
    total_point_estimate: Optional[int] = None
    total_range_low: Optional[int] = None
    total_range_high: Optional[int] = None
    math_started_at: Optional[datetime] = None
    current_question_id: Optional[int] = None
    timer_paused_at: Optional[datetime] = None
    timer_pause_seconds: int = 0
    answers: list[DiagnosticAnswerSchema] = []


class DiagnosticAttemptProgress(BaseModel):
    current_question_id: int
    math_started_at: Optional[datetime] = None
    pause_timer: Optional[bool] = None


class DiagnosticAttemptCreatedSchema(BaseModel):
    attempt_id: int
    status: str
    started_at: datetime


class DiagnosticStudentSummary(BaseModel):
    id: int
    name: str
    surname: str


class DiagnosticAttemptListItem(BaseModel):
    attempt_id: int
    student_id: int
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    rw_points: Optional[int] = None
    math_points: Optional[int] = None
    rw_scaled_estimate: Optional[int] = None
    math_scaled_estimate: Optional[int] = None
    total_point_estimate: Optional[int] = None
    total_range_low: Optional[int] = None
    total_range_high: Optional[int] = None
    student: Optional[DiagnosticStudentSummary] = None


class DiagnosticAnswerReviewSchema(BaseModel):
    order_index: int
    section: str
    domain: str
    difficulty: str
    points: int
    question_text: str
    passage_text: Optional[str] = None
    question_image: Optional[str] = None
    image_scale: float = 0.85
    answer_type: str = ANSWER_TYPE_MCQ
    choices: list[ChoiceSchema]
    selected_choice: Optional[str] = None
    response_text: Optional[str] = None
    correct_choice: Optional[str] = None
    correct_answers: Optional[list[str]] = None
    is_correct: Optional[bool] = None
    explanation: Optional[str] = None


class DiagnosticAttemptDetailSchema(BaseModel):
    attempt_id: int
    student: DiagnosticStudentSummary
    completed_at: Optional[datetime] = None
    status: str
    rw_points: Optional[int] = None
    math_points: Optional[int] = None
    rw_scaled_estimate: Optional[int] = None
    math_scaled_estimate: Optional[int] = None
    total_point_estimate: Optional[int] = None
    total_range_low: Optional[int] = None
    total_range_high: Optional[int] = None
    answers: list[DiagnosticAnswerReviewSchema]
