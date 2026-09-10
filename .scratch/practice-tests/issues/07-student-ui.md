# 07 — Student taking + review UI

**Blockers:** 04, 05
**Spec:** `docs/practice-tests-spec.md` § Frontend

- `screens/student/practice_test_list_screen.dart` — visible tests for the student's
  classes, each showing not-started / completed with score
- `screens/student/practice_test_screen.dart` — thin wrapper over the widgets
  parameterized in ticket 05; R&W 1 → R&W 2 → Math 1 → Math 2, a break between each.
  Group questions by **module id**, never by section — a section holds two modules
- Review after submit: score plus every question with the student's answer, the correct
  answer, and the explanation (dsatuz-style), via the shared
  `diagnostic_attempt_review_view` from ticket 05
- Entry point: a card in `Pages/home_page.dart` beside the diagnostic card at :359

A test already attempted shows the score and opens the review — it must not offer a retake.

**Acceptance**
- Full run: start → answer all four modules (including at least one grid-in) → submit →
  score and review render
- Timer persists across a page reload (progress patch), same as the diagnostic
- Browser devtools show no answer key in the `/questions` response during the attempt
