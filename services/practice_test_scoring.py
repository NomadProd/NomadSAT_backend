"""Scoring for practice tests: raw -> scaled, and grid-in answer matching.

Pure functions. No models, no database -- import this from routes, not the
other way round.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction

SCORE_MIN = 200
SCORE_MAX = 800
TOTAL_MIN = 400
TOTAL_MAX = 1600

# The grid-in field holds 5 characters, or 6 including a leading minus sign.
FIELD_LEN_POSITIVE = 5
FIELD_LEN_NEGATIVE = 6

def scaled_score(raw: int, max_raw: int, ceiling: int = SCORE_MAX) -> int:
    """Section raw correct count -> scaled section score."""
    # ponytail: linear 200..ceiling. Upgrade path is a per-test conversion table
    # (raw -> scaled lookup, one per module-2 variant once adaptive lands).
    if max_raw <= 0:
        return SCORE_MIN
    raw = max(0, min(raw, max_raw))
    return SCORE_MIN + round((ceiling - SCORE_MIN) * raw / max_raw)


def total_score(rw_scaled: int, math_scaled: int) -> int:
    return max(TOTAL_MIN, min(rw_scaled + math_scaled, TOTAL_MAX))


def _parse(text: str) -> Fraction | None:
    """Parse a grid-in entry to an exact rational.

    Returns None for anything not typeable into the field: symbols ($ % ,),
    mixed numbers ("3 1/2"), a bare ".", multiple separators, x/0.
    """
    text = (text or "").strip()
    if not text:
        return None

    negative = text.startswith("-")
    body = text[1:] if negative else text
    if not body or any(ch not in "0123456789./" for ch in body):
        return None

    if "/" in body:
        if body.count("/") != 1 or "." in body:
            return None
        numerator, denominator = body.split("/")
        if not numerator.isdigit() or not denominator.isdigit():
            return None
        if int(denominator) == 0:
            return None
        value = Fraction(int(numerator), int(denominator))
    else:
        if body.count(".") > 1 or body == ".":
            return None
        try:
            value = Fraction(Decimal(body))
        except (InvalidOperation, ValueError):
            return None

    return -value if negative else value


def _decimal_places(text: str) -> int | None:
    """Digits after the decimal point, or None if this isn't a decimal entry."""
    body = text.strip()
    body = body[1:] if body.startswith("-") else body
    if "/" in body or "." not in body:
        return None
    return len(body.split(".", 1)[1])


def _fills_field(text: str) -> bool:
    text = text.strip()
    limit = FIELD_LEN_NEGATIVE if text.startswith("-") else FIELD_LEN_POSITIVE
    return len(text) == limit


def spr_is_correct(response_text: str, correct_answers: list[str]) -> bool:
    """Match a student-produced response against the accepted answers.

    College Board rules:
      - up to 5 characters, or 6 with a leading minus sign
      - no %, $, commas, or mixed numbers
      - fractions need not be reduced (2/4 is correct for 1/2)
      - a decimal that does not terminate must FILL the field, truncated or
        rounded at the last character: for 2/3 the accepted entries are
        2/3, .6666, .6667, 0.666 and 0.667 -- but not .666 or 0.66
      - any one of several accepted answers is correct
    """
    response = (response_text or "").strip()
    limit = FIELD_LEN_NEGATIVE if response.startswith("-") else FIELD_LEN_POSITIVE
    if not response or len(response) > limit:
        return False

    value = _parse(response)
    if value is None:
        return False

    places = _decimal_places(response)

    for candidate in correct_answers or []:
        expected = _parse(str(candidate))
        if expected is None:
            continue
        if value == expected:
            return True
        # Approximate entry: only accepted when it fills the field, and only
        # within one unit in its last place.
        if places and _fills_field(response):
            if abs(value - expected) < Fraction(1, 10 ** places):
                return True

    return False


def is_valid_grid_in(text: str) -> bool:
    """True when `text` could be typed into the grid-in field at all.

    Used to reject unusable stored answers at authoring time, before a student
    ever sees the question.
    """
    return _parse(text) is not None
