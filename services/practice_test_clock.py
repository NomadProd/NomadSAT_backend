"""The practice-test module clock.

The server owns the clock: it decides how much time is left in a module and
whether a module is over. The browser only displays that number and counts down
between syncs, so a wrong device clock cannot buy or lose a student time.

Pure functions. No models, no database -- import this from routes, not the
other way round.
"""

from __future__ import annotations

from datetime import datetime, timezone

# How long a student may send nothing before we treat them as gone.
#
# The taking screen heartbeats every ~20s. A browser throttles a backgrounded
# tab's timers to roughly one a minute, so a tab that is merely switched away
# from still reports inside this window and keeps counting -- which is what a
# switched tab should do. A closed tab reports nothing at all, and the silence
# is what stops its clock.
AWAY_GRACE_SECONDS = 90

# Slack on the deadline for an answer, to cover the trip to the server. A
# grid-in waits 400ms for typing to stop, then the request has to cross a bad
# connection, so the honest late arrivals are seconds rather than milliseconds.
# The taking screen stops offering questions at zero regardless, so this only
# ever catches requests that were already in flight.
ANSWER_GRACE_SECONDS = 10


def as_utc(value: datetime) -> datetime:
    """A datetime in UTC, whether or not it arrived knowing its zone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _unclamped_left(
    module_started_at: datetime | None,
    time_limit_seconds: int,
    pause_seconds: int,
    now: datetime,
) -> int:
    """Seconds left, allowed to go negative -- how far past the deadline we are.

    Only the upper bound is applied here: time away cannot hand back more time
    than the module was ever worth. The lower bound is left off so callers that
    care *how* late something is can still tell.
    """
    if module_started_at is None or time_limit_seconds <= 0:
        return time_limit_seconds
    elapsed = int((as_utc(now) - as_utc(module_started_at)).total_seconds())
    left = time_limit_seconds - elapsed + max(0, int(pause_seconds or 0))
    return min(left, time_limit_seconds)


def remaining_seconds(
    *,
    module_started_at: datetime | None,
    time_limit_seconds: int,
    pause_seconds: int,
    now: datetime,
) -> int:
    """Seconds left in the module, clamped to [0, time_limit_seconds].

    This is the number the student sees. `pause_seconds` is time they spent
    away, which does not count against them.
    """
    return max(
        0,
        _unclamped_left(
            module_started_at, time_limit_seconds, pause_seconds, now
        ),
    )


def away_seconds(*, last_seen_at: datetime | None, now: datetime) -> int:
    """How much of the silence since we last heard from the student was absence.

    Only the part *past* the grace counts, never the whole gap: banking all of
    it would make the grace a cliff, where 89 seconds of silence costs the
    student nothing and 91 seconds hands back a minute and a half. Returning
    the excess keeps the grace doing its only job -- filtering out ordinary
    quiet -- and lets repeated absences add up without any state to track.
    """
    if last_seen_at is None:
        return 0
    gap = int((as_utc(now) - as_utc(last_seen_at)).total_seconds())
    return max(0, gap - AWAY_GRACE_SECONDS)


def is_expired(
    *,
    module_started_at: datetime | None,
    time_limit_seconds: int,
    pause_seconds: int,
    now: datetime,
    grace: int = ANSWER_GRACE_SECONDS,
) -> bool:
    """Whether the module is over, for the purpose of refusing work.

    Deliberately not `remaining_seconds(...) == 0`: that hits zero the instant
    the deadline passes, and an answer chosen in time but still crossing the
    network would be thrown away. The grace is the slack.
    """
    left = _unclamped_left(
        module_started_at, time_limit_seconds, pause_seconds, now
    )
    return left + max(0, grace) <= 0
