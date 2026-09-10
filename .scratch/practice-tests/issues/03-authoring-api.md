# 03 — Authoring API

**Blockers:** 01
**Spec:** `docs/practice-tests-spec.md` § API (Authoring)

New `routes/practice_tests.py` + `schemas/practice_test.py`, mounted in `main.py`
beside `diagnostic.router`.

Follow `routes/diagnostic.py` **exactly** as the pattern: one `APIRouter`, full paths
per decorator, auth from `dependencies/auth.py` (`AuthUser`, `get_current_user`,
`require_staff`, `is_admin_or_mentor`), `get_db` from `Methods/auth.py`. Do not mix in
`Methods.auth` role helpers.

Ship the authoring half only: test CRUD, class assignment, publish toggle, question
CRUD, question image upload.

- `POST /practice-tests` creates the test **and** its four modules (R&W 1-2 at
  27q/1920s, Math 1-2 at 22q/2100s) in one transaction
- `PATCH /practice-tests/{id}/visible` returns **409** unless every module holds
  exactly `required_question_count` non-deleted questions
- Question image upload mirrors `upload_diagnostic_question_image`
  (`routes/diagnostic.py:565`) — same Cloudinary service, same content-type guard
- Validate `answer_type`: `mcq` requires `choices` + `correct_choice`; `spr` requires
  a non-empty `correct_answers` and must reject `choices`. RW questions must be `mcq`.

**Acceptance**
- A teacher (not just admin/mentor) can create a test and author questions
- A student gets 403 on every endpoint in this ticket
- Publishing a 26-question R&W module 409s and the message names which of the two
  R&W modules is short; publishing a complete 98-question test succeeds
