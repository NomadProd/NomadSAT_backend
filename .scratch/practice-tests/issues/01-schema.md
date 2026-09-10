# 01 — Schema + ORM models

**Blockers:** none
**Spec:** `docs/practice-tests-spec.md` § Domain model

Add the seven `practice_test_*` tables as `migrations/20260910_practice_tests.sql`
(plain SQL, dated, applied manually — no Alembic), and the matching SQLAlchemy models
in `models.py` alongside the existing ones.

Touch nothing existing. `MockResult`, `DiagnosticQuestion` and friends are unaffected.

**Acceptance**
- Migration applies cleanly against Supabase and is idempotent-safe to re-read
- `unique(test_id, student_id)` on attempts (this is what enforces one attempt per test)
- `unique(module_id, order_index) where deleted_at is null` on questions
- Cascade deletes: test → modules → questions, attempt → answers
- Models import cleanly; `uvicorn main:app` starts
