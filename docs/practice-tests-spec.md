# Practice Tests — spec (v1 prototype)

## Name

`practice_test`, not "mock". "Mock" is already taken by a different feature:
`MockResult` / `mock_assignments.py` / `Session.mock_document` / `routes/mock_files.py`
are the **paper-mock** flow, where a student self-reports their score and uploads a
photo. Practice tests are taken in-app and auto-scored. Zero shared tables or routes.
"Practice test" is also College Board's own word for the Bluebook full-lengths.

## Scope

**In v1:** the official four-module format — Reading & Writing 1-2 (27q/32min each)
then Math 1-2 (22q/35min each), 98 questions in all.
Manual question authoring. MCQ + student-produced response (grid-in). Publish to
`Class` rows with a visible/hidden toggle. One attempt per student per test. Student
sees score + full review with explanations on submit. Staff see results only after
submit.

**Explicitly out of v1** (design must not block them, must not build them):
adaptive routing (every student sees the same module 2 for now), CSV/JSON/PDF import,
drawing questions from a shared bank, retakes, live mid-attempt monitoring.

## Domain model

New tables, all prefixed `practice_test_`. Nothing existing is modified.

```sql
practice_tests(
  id, title, description, visible bool default false,
  created_by_id -> users.id, created_at, deleted_at
)

practice_test_modules(
  id, test_id -> practice_tests.id on delete cascade,
  section text,                  -- 'reading_writing' | 'math'
  order_index int,               -- 1-2 = R&W, 3-4 = Math (presentation order)
  time_limit_seconds int,        -- 1920 (R&W) / 2100 (Math)
  required_question_count int,   -- 27 (R&W) / 22 (Math)
  unique(test_id, order_index)
)

practice_test_questions(
  id, module_id -> practice_test_modules.id on delete cascade,
  order_index int,
  domain text, difficulty text,          -- reporting/bank metadata, not scoring
  passage_text, question_text, explanation,
  question_image, question_image_public_id, image_scale float default 0.85,
  answer_type text,                      -- 'mcq' | 'spr'
  choices jsonb,                         -- [{key,text}] for mcq, null for spr
  correct_choice text,                   -- mcq only
  correct_answers jsonb,                 -- spr only, list of accepted values
  deleted_at,
  unique(module_id, order_index) where deleted_at is null
)

practice_test_classes(test_id, class_id, primary key(test_id, class_id))

practice_test_attempts(
  id, test_id, student_id, status,       -- in_progress | completed | abandoned
  started_at, completed_at,
  current_module_id, current_question_id,
  module_started_at, timer_paused_at, timer_pause_seconds default 0,
  question_ids jsonb,                    -- frozen at start, as diagnostic does
  rw_raw int, math_raw int,
  rw_scaled int, math_scaled int, total_scaled int,
  unique(test_id, student_id)            -- enforces one attempt per test
)

practice_test_answers(
  id, attempt_id -> ... on delete cascade, question_id,
  selected_choice text,                  -- mcq
  response_text text,                    -- spr, raw student input, stored verbatim
  is_correct bool, answered_at,
  unique(attempt_id, question_id)
)
```

**Why `practice_test_modules` is a table** and not a `section` column: a section holds
**two** modules, each with its own timer and its own question list, so the section
alone can never say which questions belong together. Adaptive later becomes "add a
`variant` column and more rows" — the schema does not move again.

A module's display name (`Reading & Writing 1`, `Math 2`) is derived from its position
within its own section rather than stored — see `name_modules` in
`services/practice_test_config.py`.

**No `points` column.** Scoring is raw count (below). Difficulty is metadata only.

## Scoring

Raw score = **number of correct answers in the section**, summed across both of that
section's modules — 0-54 for Reading & Writing, 0-44 for Math. Unweighted. This is what
College Board does; difficulty affects a student's score only through adaptive
routing, which v1 does not have.

```python
def scaled_score(raw: int, max_raw: int, ceiling: int = 800) -> int:
    """Raw correct count -> section scaled score."""
    # ponytail: linear 200..ceiling. Upgrade path is a per-test conversion table
    # (raw -> scaled lookup, one per module-2 variant once adaptive lands).
    if max_raw <= 0:
        return 200
    return 200 + round((ceiling - 200) * raw / max_raw)
```

`total_scaled = rw_scaled + math_scaled`, clamped to 400..1600.

The `ceiling` parameter exists now and is always 800 in v1. When adaptive lands, the
routed module-2 variant supplies a lower ceiling for the easy path — no call-site
changes.

## Grid-in (SPR) answer matching

Math only; RW questions are always `answer_type='mcq'`. Rules, per College Board's
digital SAT directions:

- Up to 5 characters, or 6 including a leading minus sign
- No `%`, `$`, commas, or mixed numbers (`3 1/2` must be `7/2` or `3.5`)
- Fractions need not be reduced — `2/4` is correct for `1/2`
- A repeating or long decimal must fill the field, **truncated or rounded** at the
  last character — for `2/3` the accepted entries are `2/3`, `.6666`, `.6667`,
  `0.666` and `0.667`; `.666` (does not fill the field) and `0.66` are rejected
- A question may declare several correct answers; any one is accepted

Implementation: parse student input and each entry of `correct_answers` to
`fractions.Fraction` and compare exactly; if that fails, accept the entry only when
it fills the field and lands within one unit in its last decimal place — one
comparison that admits both the truncated and the rounded form. Reject on parse
failure rather than raising.

This is the only non-trivial logic in the feature, so it gets
`tests/test_practice_scoring.py` covering every rule above as a case.

## API

New file `routes/practice_tests.py`, following `routes/diagnostic.py` exactly: a
single `APIRouter` with full paths spelled per decorator, auth imported from
`dependencies/auth.py` (`AuthUser`, `get_current_user`, `require_staff`,
`is_admin_or_mentor`), `get_db` from `Methods/auth.py`. Mounted in `main.py`
alongside `diagnostic.router`. Schemas in `schemas/practice_test.py`.

**Authoring** — `require_staff` (admin/mentor/teacher):

| Method | Path | Notes |
|---|---|---|
| POST | `/practice-tests` | creates the test **and** its four modules from format defaults |
| GET | `/practice-tests` | staff: all; student: visible tests for their enrolled classes |
| GET | `/practice-tests/{id}` | modules + per-module question counts |
| PATCH | `/practice-tests/{id}` | title, description |
| DELETE | `/practice-tests/{id}` | soft delete |
| PUT | `/practice-tests/{id}/classes` | replace the assigned class-id list |
| PATCH | `/practice-tests/{id}/visible` | **409** unless every module has exactly `required_question_count` live questions; the refusal names it (`The Math 2 module has 21 of 22 questions`) |
| POST | `/practice-tests/modules/{module_id}/questions` | |
| PUT | `/practice-tests/questions/{question_id}` | |
| DELETE | `/practice-tests/questions/{question_id}` | soft delete; drops a published test back to draft if the module goes short |
| POST | `/practice-tests/questions/image` | Cloudinary upload, same shape as the diagnostic's |

**Taking** — student:

| Method | Path | Notes |
|---|---|---|
| POST | `/practice-tests/{id}/attempts` | 403 if not visible to one of the student's classes; **409 if an attempt already exists** |
| GET | `/practice-tests/attempts/{id}/questions` | public shape — never leaks `correct_choice` / `correct_answers` / `explanation` |
| POST | `/practice-tests/attempts/{id}/answers` | upsert |
| PATCH | `/practice-tests/attempts/{id}/progress` | timer + current position |
| POST | `/practice-tests/attempts/{id}/complete` | scores, then unlocks the review |
| GET | `/practice-tests/attempts/{id}/detail` | review with explanations — **completed attempts only** |
| GET | `/practice-tests/attempts/me` | |
| GET | `/practice-tests/{id}/attempts` | staff; completed attempts only |

The public question serializer and the review serializer are separate functions. The
review one is reachable only when `status == 'completed'`, for the owning student or
staff with access to that student's class.

## Frontend

Package `flutter_web`. **Reuse, do not fork.** These already implement the whole
Bluebook taking experience for the diagnostic and must be parameterized rather than
copied:

- `Widgets/diagnostic_timer_bar.dart`
- `Widgets/diagnostic_question_navigator.dart`
- `Widgets/diagnostic_question_taking_view.dart`
- `Widgets/diagnostic_module_break_view.dart`
- `Widgets/diagnostic_module_review_view.dart`
- `Widgets/diagnostic_attempt_review_view.dart`
- `Widgets/diagnostic_question_figure.dart`
- `Widgets/diagnostic_math_tools.dart` (Desmos)
- `Utils/diagnostic_layout.dart`

They currently take `DiagnosticQuestion` / `DiagnosticAttempt` models directly. Extract
the minimal view-model each one actually needs so both features feed the same widget.
Copy-pasting them into `practice_test_*` twins means every future fix gets made twice.

New screens:

- `screens/admin/practice_test_list_screen.dart` — tests, publish toggle, class assignment
- `screens/admin/practice_test_editor_screen.dart` — per-module question authoring;
  the question form is `screens/admin/diagnostic_question_bank_screen.dart` generalized,
  plus the `mcq`/`spr` answer-type switch
- `screens/student/practice_test_list_screen.dart` — available tests, attempt state
- `screens/student/practice_test_screen.dart` — thin wrapper over the shared widgets
- `Services/practice_test_service.dart` — mirrors `Services/diagnostic_service.dart`

Entry points: admin from `Pages/classes_page.dart` (beside the existing question-bank
button at :122), student from `Pages/home_page.dart` (beside the diagnostic card at
:359).

**No LaTeX renderer exists in this app** — no `flutter_math`/katex in `pubspec.yaml`.
Math notation is an uploaded image (`question_image` + `image_scale`), exactly as the
diagnostic does it. Real math rendering is a separate, later ticket.

## Migrations

One dated SQL file, `migrations/20260910_practice_tests.sql`, applied manually against
Supabase like every other file in that directory. No Alembic.

## Open, deferred deliberately

- Should completed practice tests appear in `Pages/progress_history_page.dart` beside
  paper mocks and diagnostic attempts? Natural, but out of v1.
- Adaptive routing thresholds are global (decided), but unimplemented until v2.
