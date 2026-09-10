# 04 — Taking + review API

**Blockers:** 01, 02
**Spec:** `docs/practice-tests-spec.md` § API (Taking)

Second half of `routes/practice_tests.py`: attempts, answers, progress, complete,
review, listings.

Mirror the diagnostic's attempt machinery (`routes/diagnostic.py:686` onward) —
freeze `question_ids` at attempt start, same timer-pause fields, same progress patch
shape.

Differences from the diagnostic that matter:

- `POST /practice-tests/{id}/attempts` → **409 if an attempt already exists** for this
  student and test. Do not abandon-and-recreate the way the diagnostic does; one
  attempt per test is the rule.
- → **403 unless the test is `visible` and assigned to one of the student's classes**
- Scoring on complete uses `services/practice_test_scoring.py` from ticket 02:
  raw correct count per section → `scaled_score(raw, max_raw)`, total clamped 400..1600
- SPR answers store the student's raw input verbatim in `response_text`; correctness
  is computed at submit time via `spr_is_correct`

**Two separate serializers.** The public one (`/questions`) must never emit
`correct_choice`, `correct_answers` or `explanation`. The review one
(`/attempts/{id}/detail`) emits them and is reachable only when
`status == 'completed'`, for the owning student or staff with class access.

**Acceptance**
- A second `POST .../attempts` for the same student+test returns 409
- `GET /attempts/{id}/questions` response contains no answer key — assert on the raw
  JSON, not the schema
- `GET /attempts/{id}/detail` on an in-progress attempt is refused
- Staff listing shows completed attempts only
