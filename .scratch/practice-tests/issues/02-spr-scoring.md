# 02 — Scoring + grid-in answer matching

**Blockers:** none (pure logic, no DB)
**Spec:** `docs/practice-tests-spec.md` § Scoring, § Grid-in (SPR) answer matching

New `services/practice_test_scoring.py`:

- `scaled_score(raw, max_raw, ceiling=800) -> int` — linear 200..ceiling, per spec.
  Keep the `ponytail:` comment naming the conversion-table upgrade path.
- `spr_is_correct(response_text: str, correct_answers: list[str]) -> bool` —
  College Board matching rules.

Do not import models or the DB here. This file is pure functions.

**Acceptance** — `tests/test_practice_scoring.py`, run with `.venv/bin/pytest`, covering:
- `2/4` accepted for `1/2`; `0.5` accepted for `1/2`
- `.6666`, `.6667`, `0.666`, `0.667` accepted for `2/3`; `.666` and `0.66` rejected
- negative answers accepted; `3 1/2` rejected (mixed number)
- `50%`, `$5`, `1,000` rejected
- multiple `correct_answers` — any one matches
- garbage input returns False, never raises
- `scaled_score(0, 27) == 200`, `scaled_score(27, 27) == 800`
