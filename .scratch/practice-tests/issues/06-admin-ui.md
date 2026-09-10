# 06 — Admin authoring UI

**Blockers:** 03
**Spec:** `docs/practice-tests-spec.md` § Frontend

- `Services/practice_test_service.dart` — mirrors `Services/diagnostic_service.dart`
- `screens/admin/practice_test_list_screen.dart` — list, create, publish toggle,
  assign to classes (multi-select over `Class` rows)
- `screens/admin/practice_test_editor_screen.dart` — per-module question authoring.
  Generalize the form from `screens/admin/diagnostic_question_bank_screen.dart`
  (809 lines) rather than rewriting it; add the `mcq`/`spr` answer-type switch, where
  `spr` swaps the choice editor for an accepted-answers list.
- Entry point: a button in `Pages/classes_page.dart` beside the existing question-bank
  button at :122

Do not hardcode the API URL — `Services/api_config.dart` resolves it.

**Acceptance**
- A teacher can create a test, author all 49 questions, and publish it
- The publish toggle surfaces the 409 as a readable message naming which module is short
- Question images upload and preview at the right `image_scale`
