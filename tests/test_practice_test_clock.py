from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.practice_test_clock import (
    ANSWER_GRACE_SECONDS,
    AWAY_GRACE_SECONDS,
    away_seconds,
    is_expired,
    remaining_seconds,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
LIMIT = 32 * 60


def _remaining(started_minutes_ago: float, pause_seconds: int = 0) -> int:
    return remaining_seconds(
        module_started_at=NOW - timedelta(minutes=started_minutes_ago),
        time_limit_seconds=LIMIT,
        pause_seconds=pause_seconds,
        now=NOW,
    )


def _expired(started_minutes_ago: float, pause_seconds: int = 0) -> bool:
    return is_expired(
        module_started_at=NOW - timedelta(minutes=started_minutes_ago),
        time_limit_seconds=LIMIT,
        pause_seconds=pause_seconds,
        now=NOW,
    )


# --- the countdown ----------------------------------------------------------


def test_a_module_starts_at_its_full_limit():
    assert _remaining(0) == LIMIT


def test_time_runs_down():
    assert _remaining(10) == LIMIT - 600


def test_the_countdown_never_goes_negative():
    assert _remaining(90) == 0


def test_time_spent_away_does_not_count_against_the_student():
    # Ten minutes gone, six of them away: only four were theirs to lose.
    assert _remaining(10, pause_seconds=6 * 60) == LIMIT - 240


def test_being_away_cannot_hand_back_more_than_the_module_is_worth():
    assert _remaining(10, pause_seconds=60 * 60) == LIMIT


def test_a_module_that_never_started_is_worth_its_full_limit():
    assert remaining_seconds(
        module_started_at=None,
        time_limit_seconds=LIMIT,
        pause_seconds=0,
        now=NOW,
    ) == LIMIT


def test_a_naive_timestamp_is_read_as_utc():
    naive = (NOW - timedelta(minutes=10)).replace(tzinfo=None)
    assert remaining_seconds(
        module_started_at=naive,
        time_limit_seconds=LIMIT,
        pause_seconds=0,
        now=NOW,
    ) == LIMIT - 600


# --- refusing late work -----------------------------------------------------


def test_a_running_module_is_not_expired():
    assert _expired(10) is False


def test_an_answer_inside_the_grace_still_counts():
    just_over = LIMIT + ANSWER_GRACE_SECONDS - 1
    assert is_expired(
        module_started_at=NOW - timedelta(seconds=just_over),
        time_limit_seconds=LIMIT,
        pause_seconds=0,
        now=NOW,
    ) is False


def test_past_the_grace_the_module_is_over():
    well_over = LIMIT + ANSWER_GRACE_SECONDS + 1
    assert is_expired(
        module_started_at=NOW - timedelta(seconds=well_over),
        time_limit_seconds=LIMIT,
        pause_seconds=0,
        now=NOW,
    ) is True


def test_expiry_accounts_for_time_spent_away():
    # An hour gone, but fifty minutes of it away: still inside the module.
    assert _expired(60, pause_seconds=50 * 60) is False


# --- noticing the student has gone ------------------------------------------


def test_a_recent_heartbeat_is_not_an_absence():
    assert away_seconds(last_seen_at=NOW - timedelta(seconds=30), now=NOW) == 0


def test_a_throttled_background_tab_still_counts_as_present():
    # A hidden tab's timers are throttled to about one a minute.
    assert away_seconds(last_seen_at=NOW - timedelta(seconds=60), now=NOW) == 0


def test_only_the_silence_past_the_grace_is_banked():
    gap = AWAY_GRACE_SECONDS + 500
    assert away_seconds(last_seen_at=NOW - timedelta(seconds=gap), now=NOW) == 500


def test_the_grace_is_not_a_cliff():
    # A second either side of the grace must not differ by the whole grace.
    just_under = away_seconds(
        last_seen_at=NOW - timedelta(seconds=AWAY_GRACE_SECONDS - 1), now=NOW
    )
    just_over = away_seconds(
        last_seen_at=NOW - timedelta(seconds=AWAY_GRACE_SECONDS + 1), now=NOW
    )
    assert (just_under, just_over) == (0, 1)


def test_an_attempt_never_seen_banks_nothing():
    assert away_seconds(last_seen_at=None, now=NOW) == 0
