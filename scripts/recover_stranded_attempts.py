#!/usr/bin/env python
"""Score practice-test attempts that were stranded in progress.

Before the resume fix, a student who left a practice test could never get back
in, so their attempt stayed `in_progress` and was never scored. This finishes
those attempts using the *same* code path as POST /practice-tests/attempts/
{id}/complete -- the scoring is imported, never reimplemented.

Dry run (default):
    .venv/bin/python scripts/recover_stranded_attempts.py 6 7 13

Write:
    .venv/bin/python scripts/recover_stranded_attempts.py 6 7 13 --apply
"""

from __future__ import annotations

import argparse
import sys

from database import SessionLocal
from models import PracticeTestAttempt
from routes.practice_tests import _answers_for, _score_attempt
from services.practice_test_config import STATUS_COMPLETED, STATUS_IN_PROGRESS


def score(db, attempt: PracticeTestAttempt) -> dict:
    """The route's own scoring, plus what this script reports on top of it."""
    answers = _answers_for(db, attempt.id)
    return {
        **_score_attempt(db, attempt),
        # The honest timestamp: when they last worked, not when we ran this.
        "completed_at": max(
            (a.answered_at for a in answers if a.answered_at), default=None
        ),
        "answered": len(answers),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt_ids", nargs="+", type=int)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes; without it nothing is committed",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        failures = 0
        for attempt_id in args.attempt_ids:
            attempt = (
                db.query(PracticeTestAttempt)
                .filter(PracticeTestAttempt.id == attempt_id)
                .first()
            )
            if attempt is None:
                print(f"attempt {attempt_id}: NOT FOUND")
                failures += 1
                continue
            if attempt.status != STATUS_IN_PROGRESS:
                print(f"attempt {attempt_id}: skipped, status is {attempt.status!r}")
                continue

            result = score(db, attempt)
            if result["completed_at"] is None:
                print(f"attempt {attempt_id}: skipped, no answers to score")
                continue

            print(
                f"attempt {attempt_id} (student {attempt.student_id}): "
                f"{result['answered']} answers -> "
                f"RW {result['rw_raw']} ({result['rw_scaled']}), "
                f"Math {result['math_raw']} ({result['math_scaled']}), "
                f"total {result['total_scaled']}, "
                f"completed_at {result['completed_at']}"
            )

            if args.apply:
                attempt.rw_raw = result["rw_raw"]
                attempt.math_raw = result["math_raw"]
                attempt.rw_scaled = result["rw_scaled"]
                attempt.math_scaled = result["math_scaled"]
                attempt.total_scaled = result["total_scaled"]
                attempt.completed_at = result["completed_at"]
                attempt.status = STATUS_COMPLETED
                attempt.timer_paused_at = None

        if args.apply:
            db.commit()
            print("\napplied.")
        else:
            print("\ndry run -- nothing written. Re-run with --apply.")
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
