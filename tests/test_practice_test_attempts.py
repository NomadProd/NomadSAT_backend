from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

from dependencies.auth import AuthUser, get_current_user
from main import app
from Methods.auth import get_db
from models import (
    Class,
    ClassEnrollment,
    PracticeTest,
    PracticeTestAnswer,
    PracticeTestAttempt,
    PracticeTestClass,
    PracticeTestModule,
    PracticeTestQuestion,
    User,
)
from services.practice_test_config import MODULE_FORMAT, name_modules
from tests.fake_session import FakeSession as _BaseFakeSession

STUDENT_ID = 42


class FakeSession(_BaseFakeSession):
    def __init__(self):
        super().__init__(
            models=(
                PracticeTest,
                PracticeTestModule,
                PracticeTestQuestion,
                PracticeTestClass,
                PracticeTestAttempt,
                PracticeTestAnswer,
                Class,
                ClassEnrollment,
                User,
            )
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _as(role: str, user_id: int = STUDENT_ID) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthUser(id=user_id, role=role)


def _use(db: FakeSession) -> None:
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db


def _choices() -> list[dict]:
    return [{"key": k, "text": f"Choice {k}"} for k in "ABCD"]


def _build(
    db: FakeSession,
    *,
    visible: bool = True,
    enrolled: bool = True,
    per_module: int = 2,
    grid_in_last_math: bool = False,
) -> tuple[PracticeTest, list[PracticeTestModule], list[PracticeTestQuestion]]:
    """A four-module test (R&W 1-2, Math 1-2) with `per_module` questions each."""
    test = PracticeTest(title="Practice Test 1", visible=visible)
    db.add(test)
    db.add(User(id=STUDENT_ID, email="s@t.test", hashed_password="x",
                name="Ada", surname="Lovelace", role="student"))
    db.add(Class(id=1, name="Group A", archived=False, verbal_teacher_id=7))
    if enrolled:
        db.add(ClassEnrollment(class_id=1, student_id=STUDENT_ID))
        db.add(PracticeTestClass(test_id=test.id, class_id=1))

    modules, questions = [], []
    for spec in MODULE_FORMAT:
        module = PracticeTestModule(
            test_id=test.id,
            section=spec.section,
            order_index=spec.order_index,
            time_limit_seconds=spec.time_limit_seconds,
            required_question_count=spec.required_question_count,
        )
        db.add(module)
        modules.append(module)

    names = name_modules([module.section for module in modules])
    for module, name in zip(modules, names):
        is_math = module.section == "math"
        last_module = module is modules[-1]
        for order_index in range(1, per_module + 1):
            is_grid = (
                grid_in_last_math
                and last_module
                and order_index == per_module
            )
            q = PracticeTestQuestion(
                module_id=module.id, order_index=order_index,
                domain="Algebra" if is_math else "Craft and Structure",
                difficulty="easy",
                question_text=f"{name} Q{order_index}",
                answer_type="spr" if is_grid else "mcq",
                choices=None if is_grid else _choices(),
                correct_choice=None if is_grid else "B",
                correct_answers=["2/3"] if is_grid else None,
                image_scale=0.85,
                explanation=f"{name} explanation {order_index}",
            )
            db.add(q)
            questions.append(q)

    return test, modules, questions


def _start(client: TestClient, test_id: int) -> int:
    response = client.post(f"/practice-tests/{test_id}/attempts")
    assert response.status_code == 200, response.text
    return response.json()["attempt_id"]


# --- starting an attempt ----------------------------------------------------


def test_starting_an_attempt_freezes_the_question_order(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")

    body = client.post(f"/practice-tests/{test.id}/attempts").json()

    attempt = db.store[PracticeTestAttempt][0]
    assert body["status"] == "in_progress"
    assert body["current_module_id"] == modules[0].id
    assert attempt.question_ids == [q.id for q in questions]


def test_a_second_attempt_while_one_is_in_progress_conflicts(client: TestClient):
    """Retakes are allowed; two attempts running at once are not."""
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")

    _start(client, test.id)
    second = client.post(f"/practice-tests/{test.id}/attempts")

    assert second.status_code == 409
    assert "in progress" in second.json()["detail"].lower()


def test_hidden_test_cannot_be_started(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db, visible=False)
    _use(db)
    _as("student")

    assert client.post(f"/practice-tests/{test.id}/attempts").status_code == 403


def test_test_for_another_group_cannot_be_started(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db, enrolled=False)
    _use(db)
    _as("student")

    assert client.post(f"/practice-tests/{test.id}/attempts").status_code == 403


def test_test_without_questions_cannot_be_started(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db, per_module=0)
    _use(db)
    _as("student")

    response = client.post(f"/practice-tests/{test.id}/attempts")
    assert response.status_code == 409
    assert "no questions" in response.json()["detail"]


# --- the answer key must never leak -----------------------------------------


def test_attempt_questions_carry_no_answer_key(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db, grid_in_last_math=True)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    response = client.get(f"/practice-tests/attempts/{attempt_id}/questions")

    assert response.status_code == 200
    raw = response.text
    for leak in ("correct_choice", "correct_answers", "explanation"):
        assert leak not in raw, f"{leak} leaked to the student"
    assert len(response.json()) == 8  # four modules, two questions each


def test_submitting_an_answer_does_not_reveal_correctness(client: TestClient):
    db = FakeSession()
    test, _m, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    response = client.post(
        f"/practice-tests/attempts/{attempt_id}/answers",
        json={"question_id": questions[0].id, "selected_choice": "b"},
    )

    assert response.status_code == 200
    assert "is_correct" not in response.text
    assert response.json()["selected_choice"] == "B"
    assert db.store[PracticeTestAnswer][0].is_correct is True


def test_another_student_cannot_read_the_questions(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    _as("student", user_id=999)
    assert client.get(
        f"/practice-tests/attempts/{attempt_id}/questions"
    ).status_code == 403


# --- answering --------------------------------------------------------------


def test_answering_twice_updates_the_same_row(client: TestClient):
    db = FakeSession()
    test, _m, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    url = f"/practice-tests/attempts/{attempt_id}/answers"
    client.post(url, json={"question_id": questions[0].id, "selected_choice": "A"})
    client.post(url, json={"question_id": questions[0].id, "selected_choice": "B"})

    rows = db.store[PracticeTestAnswer]
    assert len(rows) == 1
    assert rows[0].selected_choice == "B"
    assert rows[0].is_correct is True


def test_grid_in_answer_is_graded_by_the_college_board_rules(client: TestClient):
    db = FakeSession()
    test, _m, questions = _build(db, grid_in_last_math=True)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    grid_in = questions[-1]

    url = f"/practice-tests/attempts/{attempt_id}/answers"
    client.post(url, json={"question_id": grid_in.id, "response_text": "0.66"})
    assert db.store[PracticeTestAnswer][0].is_correct is False

    client.post(url, json={"question_id": grid_in.id, "response_text": "0.667"})
    assert db.store[PracticeTestAnswer][0].is_correct is True


def test_answer_shape_must_match_the_question_type(client: TestClient):
    db = FakeSession()
    test, _m, questions = _build(db, grid_in_last_math=True)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    url = f"/practice-tests/attempts/{attempt_id}/answers"

    assert client.post(
        url, json={"question_id": questions[0].id, "response_text": "1/2"}
    ).status_code == 422
    assert client.post(
        url, json={"question_id": questions[-1].id, "selected_choice": "A"}
    ).status_code == 422


def test_a_question_outside_the_attempt_is_not_answerable(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    assert client.post(
        f"/practice-tests/attempts/{attempt_id}/answers",
        json={"question_id": 9999, "selected_choice": "A"},
    ).status_code == 404


# --- progress ---------------------------------------------------------------


def test_moving_to_the_next_module_restarts_the_module_timer(client: TestClient):
    db = FakeSession()
    test, modules, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    attempt = db.store[PracticeTestAttempt][0]
    attempt.timer_pause_seconds = 90

    response = client.patch(
        f"/practice-tests/attempts/{attempt_id}/progress",
        json={"current_module_id": modules[1].id},
    )

    assert response.status_code == 200
    assert response.json()["current_module_id"] == modules[1].id
    assert response.json()["timer_pause_seconds"] == 0
    assert attempt.module_started_at is not None


def test_a_finished_module_cannot_be_revisited(client: TestClient):
    db = FakeSession()
    test, modules, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    url = f"/practice-tests/attempts/{attempt_id}/progress"

    client.patch(url, json={"current_module_id": modules[1].id})
    back = client.patch(url, json={"current_module_id": modules[0].id})

    assert back.status_code == 409


def test_pausing_and_resuming_accumulates_paused_seconds(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    url = f"/practice-tests/attempts/{attempt_id}/progress"

    paused = client.patch(url, json={"pause_timer": True})
    assert paused.json()["timer_paused_at"] is not None

    resumed = client.patch(url, json={"pause_timer": False})
    assert resumed.json()["timer_paused_at"] is None
    assert resumed.json()["timer_pause_seconds"] >= 0


def test_progress_is_rejected_once_the_attempt_is_complete(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    client.post(f"/practice-tests/attempts/{attempt_id}/complete")

    assert client.patch(
        f"/practice-tests/attempts/{attempt_id}/progress",
        json={"pause_timer": True},
    ).status_code == 409


# --- completing -------------------------------------------------------------


def test_completing_scores_each_section_from_raw_correct_counts(client: TestClient):
    db = FakeSession()
    test, _m, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    url = f"/practice-tests/attempts/{attempt_id}/answers"

    # Questions 0-3 are the two Reading & Writing modules, 4-7 the two Math ones.
    # One R&W answer is wrong; everything else is right.
    for index, question in enumerate(questions):
        client.post(url, json={
            "question_id": question.id,
            "selected_choice": "A" if index == 3 else "B",
        })

    body = client.post(f"/practice-tests/attempts/{attempt_id}/complete").json()

    assert body["status"] == "completed"
    # Raw counts are per section, so both modules of a section add up.
    assert (body["rw_raw"], body["math_raw"]) == (3, 4)
    assert (body["rw_scaled"], body["math_scaled"]) == (650, 800)
    assert body["total_scaled"] == 1450


def test_unanswered_questions_count_as_incorrect(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    body = client.post(f"/practice-tests/attempts/{attempt_id}/complete").json()

    assert body["total_scaled"] == 400


def test_completing_twice_conflicts(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    client.post(f"/practice-tests/attempts/{attempt_id}/complete")
    again = client.post(f"/practice-tests/attempts/{attempt_id}/complete")

    assert again.status_code == 409


# --- review -----------------------------------------------------------------


def test_review_is_refused_while_the_attempt_is_in_progress(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    response = client.get(f"/practice-tests/attempts/{attempt_id}/detail")

    assert response.status_code == 409
    assert "completed" in response.json()["detail"]


def test_review_after_submit_shows_the_answer_key_and_explanations(client: TestClient):
    db = FakeSession()
    test, _m, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    client.post(
        f"/practice-tests/attempts/{attempt_id}/answers",
        json={"question_id": questions[0].id, "selected_choice": "A"},
    )
    client.post(f"/practice-tests/attempts/{attempt_id}/complete")

    body = client.get(f"/practice-tests/attempts/{attempt_id}/detail").json()

    assert body["student"]["name"] == "Ada"
    assert len(body["items"]) == 8
    assert [item["section"] for item in body["items"]] == (
        ["reading_writing"] * 4 + ["math"] * 4
    )
    first = body["items"][0]
    assert first["selected_choice"] == "A"
    assert first["is_correct"] is False
    assert first["question"]["correct_choice"] == "B"
    assert first["question"]["explanation"] == "Reading & Writing 1 explanation 1"
    assert body["items"][1]["is_correct"] is False


def test_another_student_cannot_read_the_review(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    client.post(f"/practice-tests/attempts/{attempt_id}/complete")

    _as("student", user_id=999)
    assert client.get(
        f"/practice-tests/attempts/{attempt_id}/detail"
    ).status_code == 403


def test_teacher_of_the_students_class_can_read_the_review(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    client.post(f"/practice-tests/attempts/{attempt_id}/complete")

    _as("teacher", user_id=7)
    assert client.get(
        f"/practice-tests/attempts/{attempt_id}/detail"
    ).status_code == 200

    _as("teacher", user_id=8)
    assert client.get(
        f"/practice-tests/attempts/{attempt_id}/detail"
    ).status_code == 403


# --- listings ---------------------------------------------------------------


def test_my_attempts_lists_only_my_own(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    _start(client, test.id)
    db.add(PracticeTestAttempt(test_id=test.id, student_id=999, status="completed"))

    body = client.get("/practice-tests/attempts/me").json()

    assert len(body) == 1
    assert body[0]["student_id"] == STUDENT_ID
    assert body[0]["test_title"] == "Practice Test 1"


def test_staff_listing_shows_completed_attempts_only(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    _as("teacher", user_id=7)
    assert client.get(f"/practice-tests/{test.id}/attempts").json() == []

    _as("student")
    client.post(f"/practice-tests/attempts/{attempt_id}/complete")

    _as("teacher", user_id=7)
    body = client.get(f"/practice-tests/{test.id}/attempts").json()
    assert len(body) == 1
    assert body[0]["status"] == "completed"
    assert body[0]["student"]["surname"] == "Lovelace"


def test_students_cannot_read_the_staff_listing(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")

    assert client.get(f"/practice-tests/{test.id}/attempts").status_code == 403


def test_a_teacher_outside_the_class_sees_no_attempts(client: TestClient):
    db = FakeSession()
    test, _m, _q = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    client.post(f"/practice-tests/attempts/{attempt_id}/complete")

    _as("teacher", user_id=8)
    assert client.get(f"/practice-tests/{test.id}/attempts").json() == []


# --- reading an attempt in progress (resume) --------------------------------


def test_the_owner_can_read_their_in_progress_attempt_with_its_answers(
    client: TestClient,
):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    client.post(
        f"/practice-tests/attempts/{attempt_id}/answers",
        json={"question_id": questions[0].id, "selected_choice": "B"},
    )

    response = client.get(f"/practice-tests/attempts/{attempt_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "in_progress"
    answers = {item["question_id"]: item for item in body["answers"]}
    assert questions[0].id in answers, "the saved answer must come back for resume"
    assert answers[questions[0].id]["selected_choice"] == "B"


def test_an_in_progress_attempt_hides_correctness(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    for question in questions[:2]:
        client.post(
            f"/practice-tests/attempts/{attempt_id}/answers",
            json={"question_id": question.id, "selected_choice": "B"},
        )

    body = client.get(f"/practice-tests/attempts/{attempt_id}").json()

    assert body["answers"], "expected the answers to be returned"
    assert all(item["is_correct"] is None for item in body["answers"]), (
        "correctness must stay hidden until the attempt is completed"
    )


def test_another_student_cannot_read_the_attempt(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    _as("student", user_id=STUDENT_ID + 1)
    response = client.get(f"/practice-tests/attempts/{attempt_id}")

    assert response.status_code == 403, response.text


def test_a_completed_attempt_shows_correctness(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)
    client.post(
        f"/practice-tests/attempts/{attempt_id}/answers",
        json={"question_id": questions[0].id, "selected_choice": "B"},
    )
    client.post(f"/practice-tests/attempts/{attempt_id}/complete")

    body = client.get(f"/practice-tests/attempts/{attempt_id}").json()

    assert body["status"] == "completed"
    graded = {item["question_id"]: item["is_correct"] for item in body["answers"]}
    assert graded[questions[0].id] is True, (
        "once submitted the student may see which answers were right"
    )


def test_the_students_teacher_can_read_the_attempt(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    _as("teacher", user_id=7)
    response = client.get(f"/practice-tests/attempts/{attempt_id}")

    assert response.status_code == 200, response.text


def test_an_unrelated_teacher_cannot_read_the_attempt(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    _as("teacher", user_id=404)
    response = client.get(f"/practice-tests/attempts/{attempt_id}")

    assert response.status_code == 403, response.text


def test_admin_can_read_any_attempt(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    _as("admin", user_id=1)
    assert client.get(f"/practice-tests/attempts/{attempt_id}").status_code == 200


def test_reading_a_missing_attempt_is_a_404(client: TestClient):
    db = FakeSession()
    _build(db)
    _use(db)
    _as("student")

    assert client.get("/practice-tests/attempts/424242").status_code == 404


def test_attempts_me_is_not_parsed_as_an_attempt_id(client: TestClient):
    """The literal path /attempts/me must not be swallowed by /attempts/{id}."""
    db = FakeSession()
    _build(db)
    _use(db)
    _as("student")

    response = client.get("/practice-tests/attempts/me")

    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)


# --- retakes ----------------------------------------------------------------


def test_a_finished_test_can_be_retaken(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    first = _start(client, test.id)
    client.post(f"/practice-tests/attempts/{first}/complete")

    response = client.post(f"/practice-tests/{test.id}/attempts")

    assert response.status_code == 200, response.text
    assert response.json()["attempt_id"] != first


def test_a_retake_keeps_the_earlier_attempt(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    first = _start(client, test.id)
    client.post(
        f"/practice-tests/attempts/{first}/answers",
        json={"question_id": questions[0].id, "selected_choice": "B"},
    )
    client.post(f"/practice-tests/attempts/{first}/complete")
    second = client.post(f"/practice-tests/{test.id}/attempts").json()["attempt_id"]

    mine = client.get("/practice-tests/attempts/me").json()

    ids = {item["id"] for item in mine}
    assert {first, second} <= ids, "the earlier score must stay in the history"
    earlier = next(item for item in mine if item["id"] == first)
    assert earlier["status"] == "completed"
    assert earlier["total_scaled"] is not None


def test_staff_see_one_row_per_student_with_their_best(client: TestClient):
    db = FakeSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    first = _start(client, test.id)
    client.post(f"/practice-tests/attempts/{first}/complete")
    second = client.post(f"/practice-tests/{test.id}/attempts").json()["attempt_id"]
    for question in questions:
        client.post(
            f"/practice-tests/attempts/{second}/answers",
            json={"question_id": question.id, "selected_choice": "B"},
        )
    client.post(f"/practice-tests/attempts/{second}/complete")

    _as("admin", user_id=1)
    rows = client.get(f"/practice-tests/{test.id}/attempts").json()

    assert len(rows) == 1, "a retake must not add a second row for the same student"
    assert rows[0]["id"] == second, "the better attempt is the one that counts"


# --- concurrent answers -----------------------------------------------------


class _RacingSession(FakeSession):
    """Loses one race: the first answer commit fails the way Postgres would.

    Mirrors two requests for the same question arriving together -- both find no
    row, both insert, and the unique index rejects the loser.
    """

    def __init__(self):
        super().__init__()
        self.raised = False
        self.rolled_back = False

    def commit(self):
        pending = [
            row
            for row in self.store.get(PracticeTestAnswer, [])
            if row.answered_at is not None
        ]
        if pending and not self.raised:
            self.raised = True
            # The request that won the race already stored its row.
            loser = pending[-1]
            self.store[PracticeTestAnswer].remove(loser)
            winner = PracticeTestAnswer(
                attempt_id=loser.attempt_id,
                question_id=loser.question_id,
            )
            winner.selected_choice = "A"
            winner.is_correct = False
            winner.answered_at = _utcnow_for_tests()
            super().add(winner)
            raise IntegrityError("duplicate key", None, Exception("23505"))
        return super().commit()

    def rollback(self):
        self.rolled_back = True
        return super().rollback()


def _utcnow_for_tests():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def test_two_answers_to_the_same_question_at_once_do_not_500(client: TestClient):
    db = _RacingSession()
    test, modules, questions = _build(db)
    _use(db)
    _as("student")
    attempt_id = _start(client, test.id)

    response = client.post(
        f"/practice-tests/attempts/{attempt_id}/answers",
        json={"question_id": questions[0].id, "selected_choice": "B"},
    )

    assert response.status_code == 200, response.text
    assert db.raised and db.rolled_back, "the race must actually have been hit"
    rows = [
        row
        for row in db.store[PracticeTestAnswer]
        if row.question_id == questions[0].id
    ]
    assert len(rows) == 1, "a race must not leave two rows for one question"
    assert rows[0].selected_choice == "B", (
        "the answer the student actually gave must survive the race"
    )
