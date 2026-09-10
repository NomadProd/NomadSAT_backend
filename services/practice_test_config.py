"""Shape constants for practice tests. Do not scatter these in route or UI code."""

from __future__ import annotations

from dataclasses import dataclass

SECTION_READING_WRITING = "reading_writing"
SECTION_MATH = "math"
SECTIONS = (SECTION_READING_WRITING, SECTION_MATH)

SECTION_LABELS = {
    SECTION_READING_WRITING: "Reading & Writing",
    SECTION_MATH: "Math",
}

DIFFICULTY_EASY = "easy"
DIFFICULTY_MEDIUM = "medium"
DIFFICULTY_HARD = "hard"
DIFFICULTIES = (DIFFICULTY_EASY, DIFFICULTY_MEDIUM, DIFFICULTY_HARD)

ANSWER_TYPE_MCQ = "mcq"
ANSWER_TYPE_SPR = "spr"
ANSWER_TYPES = (ANSWER_TYPE_MCQ, ANSWER_TYPE_SPR)

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"


@dataclass(frozen=True)
class ModuleFormat:
    section: str
    order_index: int
    time_limit_seconds: int
    required_question_count: int


# Official digital SAT module format: two modules per section, 98 questions in all.
# v1 is non-adaptive -- every student sees the same second module. Adaptive adds a
# `variant` column and two more module-2 rows per section; this tuple is what
# changes, not the schema.
MODULE_FORMAT = (
    ModuleFormat(SECTION_READING_WRITING, 1, 32 * 60, 27),
    ModuleFormat(SECTION_READING_WRITING, 2, 32 * 60, 27),
    ModuleFormat(SECTION_MATH, 3, 35 * 60, 22),
    ModuleFormat(SECTION_MATH, 4, 35 * 60, 22),
)


def section_allows_grid_in(section: str) -> bool:
    """Student-produced responses exist only in Math on the digital SAT."""
    return section == SECTION_MATH


def name_modules(sections: list[str]) -> list[str]:
    """Label each module by its position within its own section.

    Given the sections of a test's modules in presentation order, returns
    ["Reading & Writing 1", "Reading & Writing 2", "Math 1", "Math 2"].
    """
    seen: dict[str, int] = {}
    names = []
    for section in sections:
        seen[section] = seen.get(section, 0) + 1
        names.append(f"{SECTION_LABELS.get(section, section)} {seen[section]}")
    return names
