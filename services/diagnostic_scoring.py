from __future__ import annotations

import math
from typing import TypedDict

from services.diagnostic_config import (
    CURVE_K,
    CURVE_SCALE,
    CURVE_SPREAD,
    SECTION_MIDPOINT,
    SECTION_SCORE_MAX,
    SECTION_SCORE_MIN,
    SEM,
    TOTAL_SCORE_MAX,
    TOTAL_SCORE_MIN,
)


class DiagnosticEstimate(TypedDict):
    rw_scaled_estimate: int
    math_scaled_estimate: int
    total_point_estimate: int
    total_range_low: int
    total_range_high: int


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round_to_nearest_10(value: float) -> int:
    return int(math.floor(value / 10.0 + 0.5) * 10)


def section_center(points: int) -> int:
    theta = (points - SECTION_MIDPOINT) / CURVE_SCALE
    raw = 500 + CURVE_SPREAD * math.tanh(theta * CURVE_K)
    return round_to_nearest_10(clip(raw, SECTION_SCORE_MIN, SECTION_SCORE_MAX))


def estimate_result(rw_points: int, math_points: int) -> DiagnosticEstimate:
    rw_center = section_center(rw_points)
    math_center = section_center(math_points)
    combined_sem = math.sqrt(SEM ** 2 + SEM ** 2)
    total_estimate = rw_center + math_center
    total_low = clip(total_estimate - combined_sem, TOTAL_SCORE_MIN, TOTAL_SCORE_MAX)
    total_high = clip(total_estimate + combined_sem, TOTAL_SCORE_MIN, TOTAL_SCORE_MAX)
    return {
        "rw_scaled_estimate": rw_center,
        "math_scaled_estimate": math_center,
        "total_point_estimate": total_estimate,
        "total_range_low": round_to_nearest_10(total_low),
        "total_range_high": round_to_nearest_10(total_high),
    }
