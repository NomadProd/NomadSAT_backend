# 05 — Parameterize the shared taking widgets

**Blockers:** none (frontend, independent of the API work)
**Spec:** `docs/practice-tests-spec.md` § Frontend

The diagnostic already implements the entire Bluebook taking experience. Make those
widgets feature-agnostic so practice tests reuse them instead of forking them.

In `NomadSAT_frontend`, these currently bind to `DiagnosticQuestion` /
`DiagnosticAttempt` directly:

- `Widgets/diagnostic_timer_bar.dart`
- `Widgets/diagnostic_question_navigator.dart`
- `Widgets/diagnostic_question_taking_view.dart`
- `Widgets/diagnostic_module_break_view.dart`
- `Widgets/diagnostic_module_review_view.dart`
- `Widgets/diagnostic_attempt_review_view.dart`
- `Widgets/diagnostic_question_figure.dart`
- `Widgets/diagnostic_math_tools.dart`
- `Utils/diagnostic_layout.dart`

Extract the minimal view-model each widget actually needs and have both features feed
it. Resist inventing a general framework — take exactly the fields the widgets read.

Also add SPR support to the taking view: an `mcq`/`spr` switch where `spr` renders a
text input with the 5/6-character limit and rejects `%`, `$`, `,` on entry.

**This is a pure refactor of existing behaviour.** The diagnostic must work identically
afterwards.

**Acceptance**
- `flutter test` passes, `test/diagnostic_test.dart` included
- The diagnostic flow is unchanged end to end: start, answer, module break, submit, review
- No `practice_test_*` copy of any widget above exists
