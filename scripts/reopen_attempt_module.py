#!/usr/bin/env python
"""Give an in-progress attempt a fresh, proportional clock on its current module.

For a student the lock-out bug stopped part-way through a module: their answers
are intact, but the module timer expired while they were locked out, so on
resume they can only submit. This hands that one module a new allowance sized to
the questions they have left, and leaves the clock *paused* so it starts when
they open the test rather than the moment this runs.

Nothing else is touched: no answers, no scores, no status change. The student
finishes and submits through the normal endpoint.

Dry run (default):
    PYTHONPATH=. .venv/bin/python scripts/reopen_attempt_module.py 6 --minutes 13

Write:
    PYTHONPATH=. .venv/bin/python scripts/reopen_attempt_module.py 6 --minutes 13 --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from database import SessionLocal
from models import PracticeTestAttempt, PracticeTestModule
from routes.practice_tests import _answers_for, _questions_for_attempt
from services.practice_test_config import STATUS_IN_PROGRESS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt_id", type=int)
    parser.add_argument(
        "--minutes",
        type=float,
        required=True,
        help="minutes to grant on the current module",
    )
    parser.add_argument("--apply", action="store_true", help="write the change")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        attempt = (
            db.query(PracticeTestAttempt)
            .filter(PracticeTestAttempt.id == args.attempt_id)
            .first()
        )
        if attempt is None:
            print(f"attempt {args.attempt_id}: NOT FOUND")
            return 1
        if attempt.status != STATUS_IN_PROGRESS:
            print(
                f"attempt {args.attempt_id}: refusing, status is "
                f"{attempt.status!r} -- this must not reopen a finished test"
            )
            return 1

        module = (
            db.query(PracticeTestModule)
            .filter(PracticeTestModule.id == attempt.current_module_id)
            .first()
        )
        if module is None:
            print(f"attempt {args.attempt_id}: no current module to reopen")
            return 1

        answered = {answer.question_id for answer in _answers_for(db, attempt.id)}
        unanswered = [
            question
            for question in _questions_for_attempt(db, attempt)
            if question.module_id == module.id and question.id not in answered
        ]
        unanswered.sort(key=lambda q: q.order_index)
        if not unanswered:
            print(
                f"attempt {args.attempt_id}: refusing, every question in module "
                f"{module.order_index} is already answered"
            )
            return 1

        now = datetime.now(timezone.utc)
        granted = timedelta(minutes=args.minutes)
        limit = timedelta(seconds=module.time_limit_seconds)
        if granted > limit:
            print(
                f"attempt {args.attempt_id}: refusing, {args.minutes} minutes is "
                f"more than the module's own {limit.total_seconds() / 60:g}"
            )
            return 1
        started_at = now - (limit - granted)
        landing = unanswered[0]

        print(f"attempt {attempt.id}, student {attempt.student_id}")
        print(f"  module        : {module.section} #{module.order_index}")
        print(
            f"  unanswered    : {len(unanswered)} "
            f"(order_index {', '.join(str(q.order_index) for q in unanswered)})"
        )
        print(f"  lands on      : question {landing.order_index} (id {landing.id})")
        print(f"  grants        : {args.minutes:g} of "
              f"{limit.total_seconds() / 60:g} minutes, paused until they open it")
        print("  before        :"
              f" module_started_at={attempt.module_started_at}"
              f" timer_paused_at={attempt.timer_paused_at}"
              f" timer_pause_seconds={attempt.timer_pause_seconds}"
              f" current_question_id={attempt.current_question_id}")

        if args.apply:
            attempt.module_started_at = started_at
            attempt.timer_paused_at = now
            attempt.timer_pause_seconds = 0
            attempt.current_question_id = landing.id
            db.commit()
            db.refresh(attempt)
            print("  after         :"
                  f" module_started_at={attempt.module_started_at}"
                  f" timer_paused_at={attempt.timer_paused_at}"
                  f" timer_pause_seconds={attempt.timer_pause_seconds}"
                  f" current_question_id={attempt.current_question_id}")
            print("\napplied.")
        else:
            print("\ndry run -- nothing written. Re-run with --apply.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
