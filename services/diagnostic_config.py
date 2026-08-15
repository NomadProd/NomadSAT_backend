from __future__ import annotations

from dataclasses import dataclass

# Scoring curve constants — tune here after calibration with official SAT scores.
# Do not scatter these values in route or UI code.
SECTION_MAX = 50
SECTION_MIDPOINT = 25
CURVE_SCALE = 10
CURVE_K = 0.55
CURVE_SPREAD = 300
SEM = 55

SECTION_SCORE_MIN = 200
SECTION_SCORE_MAX = 800
TOTAL_SCORE_MIN = 400
TOTAL_SCORE_MAX = 1600

# Section timing (frontend is primary; backend uses a soft completion window).
RW_SECTION_MINUTES = 12
MATH_SECTION_MINUTES = 15
COMPLETION_GRACE_MINUTES = 5
SOFT_COMPLETION_LIMIT_MINUTES = (
    RW_SECTION_MINUTES + MATH_SECTION_MINUTES + COMPLETION_GRACE_MINUTES
)

SECTION_READING_WRITING = "reading_writing"
SECTION_MATH = "math"

DIFFICULTY_EASY = "easy"
DIFFICULTY_MEDIUM = "medium"
DIFFICULTY_HARD = "hard"

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"

QUESTION_COUNT = 20
RW_ORDER_INDEXES = range(1, 11)
MATH_ORDER_INDEXES = range(11, 21)


@dataclass(frozen=True)
class QuestionSlot:
    order_index: int
    section: str
    domain: str
    difficulty: str
    points: int


# Fixed 20-question diagnostic layout. order_index is the presentation order.
QUESTION_LAYOUT: dict[int, QuestionSlot] = {
    1: QuestionSlot(1, SECTION_READING_WRITING, "Craft and Structure", DIFFICULTY_EASY, 3),
    2: QuestionSlot(2, SECTION_READING_WRITING, "Craft and Structure", DIFFICULTY_MEDIUM, 5),
    3: QuestionSlot(3, SECTION_READING_WRITING, "Craft and Structure", DIFFICULTY_HARD, 7),
    4: QuestionSlot(4, SECTION_READING_WRITING, "Information and Ideas", DIFFICULTY_MEDIUM, 5),
    5: QuestionSlot(5, SECTION_READING_WRITING, "Information and Ideas", DIFFICULTY_HARD, 7),
    6: QuestionSlot(6, SECTION_READING_WRITING, "Standard English Conventions", DIFFICULTY_EASY, 3),
    7: QuestionSlot(7, SECTION_READING_WRITING, "Standard English Conventions", DIFFICULTY_MEDIUM, 5),
    8: QuestionSlot(8, SECTION_READING_WRITING, "Expression of Ideas", DIFFICULTY_EASY, 3),
    9: QuestionSlot(9, SECTION_READING_WRITING, "Expression of Ideas", DIFFICULTY_MEDIUM, 5),
    10: QuestionSlot(10, SECTION_READING_WRITING, "Expression of Ideas", DIFFICULTY_HARD, 7),
    11: QuestionSlot(11, SECTION_MATH, "Algebra", DIFFICULTY_EASY, 3),
    12: QuestionSlot(12, SECTION_MATH, "Advanced Math", DIFFICULTY_EASY, 3),
    13: QuestionSlot(13, SECTION_MATH, "Geometry and Trigonometry", DIFFICULTY_EASY, 3),
    14: QuestionSlot(14, SECTION_MATH, "Algebra", DIFFICULTY_MEDIUM, 5),
    15: QuestionSlot(15, SECTION_MATH, "Advanced Math", DIFFICULTY_MEDIUM, 5),
    16: QuestionSlot(16, SECTION_MATH, "Problem-Solving and Data Analysis", DIFFICULTY_MEDIUM, 5),
    17: QuestionSlot(17, SECTION_MATH, "Problem-Solving and Data Analysis", DIFFICULTY_MEDIUM, 5),
    18: QuestionSlot(18, SECTION_MATH, "Advanced Math", DIFFICULTY_HARD, 7),
    19: QuestionSlot(19, SECTION_MATH, "Problem-Solving and Data Analysis", DIFFICULTY_HARD, 7),
    20: QuestionSlot(20, SECTION_MATH, "Geometry and Trigonometry", DIFFICULTY_HARD, 7),
}


def slot_for_order_index(order_index: int) -> QuestionSlot | None:
    return QUESTION_LAYOUT.get(order_index)


def layout_mismatch_message(
    order_index: int,
    section: str,
    domain: str,
    difficulty: str,
    points: int,
) -> str | None:
    slot = slot_for_order_index(order_index)
    if slot is None:
        return "order_index must be between 1 and 20"
    if section != slot.section:
        expected = "reading_writing" if order_index <= 10 else "math"
        return (
            f"order_index {order_index} must use section '{expected}'"
        )
    if domain != slot.domain:
        return (
            f"order_index {order_index} must use domain '{slot.domain}'"
        )
    if difficulty != slot.difficulty or points != slot.points:
        return (
            f"order_index {order_index} must be {slot.difficulty} "
            f"with {slot.points} points"
        )
    return None
