from __future__ import annotations

from services.diagnostic_config import (
    QUESTION_LAYOUT,
    layout_mismatch_message,
)
from services.diagnostic_scoring import (
    estimate_result,
    round_to_nearest_10,
    section_center,
)


def test_layout_has_twenty_slots_and_fifty_points_per_section():
    assert len(QUESTION_LAYOUT) == 20
    rw = [slot for slot in QUESTION_LAYOUT.values() if slot.section == "reading_writing"]
    math = [slot for slot in QUESTION_LAYOUT.values() if slot.section == "math"]
    assert len(rw) == 10
    assert len(math) == 10
    assert sum(slot.points for slot in rw) == 50
    assert sum(slot.points for slot in math) == 50
    assert [slot.order_index for slot in rw] == list(range(1, 11))
    assert [slot.order_index for slot in math] == list(range(11, 21))


def test_layout_rejects_hard_question_with_easy_points():
    message = layout_mismatch_message(
        order_index=3,
        section="reading_writing",
        domain="Craft and Structure",
        difficulty="hard",
        points=3,
    )
    assert message is not None
    assert "hard" in message
    assert "7" in message


def test_layout_rejects_wrong_section_for_math_slot():
    message = layout_mismatch_message(
        order_index=11,
        section="reading_writing",
        domain="Algebra",
        difficulty="easy",
        points=3,
    )
    assert message is not None
    assert "math" in message


def test_layout_accepts_exact_slot():
    slot = QUESTION_LAYOUT[16]
    assert layout_mismatch_message(
        order_index=slot.order_index,
        section=slot.section,
        domain=slot.domain,
        difficulty=slot.difficulty,
        points=slot.points,
    ) is None


def test_section_center_midpoint_is_500():
    assert section_center(25) == 500


def test_section_center_bounds_and_rounding():
    assert section_center(0) == 240
    assert section_center(50) == 760
    assert section_center(0) % 10 == 0
    assert section_center(50) % 10 == 0


def test_round_to_nearest_10():
    assert round_to_nearest_10(402.22) == 400
    assert round_to_nearest_10(1442.22) == 1440
    assert round_to_nearest_10(1597.78) == 1600


def test_estimate_result_all_correct():
    result = estimate_result(50, 50)
    assert result["rw_scaled_estimate"] == 760
    assert result["math_scaled_estimate"] == 760
    assert result["total_point_estimate"] == 1520
    assert result["total_range_low"] == 1440
    assert result["total_range_high"] == 1600


def test_estimate_result_all_incorrect():
    result = estimate_result(0, 0)
    assert result["rw_scaled_estimate"] == 240
    assert result["math_scaled_estimate"] == 240
    assert result["total_point_estimate"] == 480
    assert result["total_range_low"] == 400
    assert result["total_range_high"] == 560


def test_estimate_result_perfect_rw_zero_math():
    result = estimate_result(50, 0)
    assert result["rw_scaled_estimate"] == 760
    assert result["math_scaled_estimate"] == 240
    assert result["total_point_estimate"] == 1000
    assert result["total_range_low"] == 920
    assert result["total_range_high"] == 1080
