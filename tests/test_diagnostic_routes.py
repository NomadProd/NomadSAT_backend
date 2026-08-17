from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from dependencies.auth import AuthUser, get_current_user
from main import app
from Methods.auth import get_db
from models import DiagnosticAnswer, DiagnosticAttempt, DiagnosticQuestion
from services.diagnostic_config import QUESTION_LAYOUT
from services.diagnostic_scoring import estimate_result


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _FakeQuery:
    def __init__(self, session: "FakeSession", model, rows):
        self.session = session
        self.model = model
        self.rows = list(rows)

    def filter(self, *clauses):
        rows = self.rows
        for clause in clauses:
            rows = [row for row in rows if self.session.matches(row, clause)]
        return _FakeQuery(self.session, self.model, rows)

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)

    def count(self):
        return len(self.rows)


class FakeSession:
    def __init__(self):
        self.store = {
            DiagnosticQuestion: [],
            DiagnosticAttempt: [],
            DiagnosticAnswer: [],
        }
        self.counters = {
            DiagnosticQuestion: 1,
            DiagnosticAttempt: 1,
            DiagnosticAnswer: 1,
        }
        self.committed = False

    def query(self, model):
        return _FakeQuery(self, model, self.store.get(model, []))

    def add(self, obj):
        model = type(obj)
        if getattr(obj, "id", None) is None:
            obj.id = self.counters[model]
            self.counters[model] += 1
        bucket = self.store.setdefault(model, [])
        if obj not in bucket:
            bucket.append(obj)

    def delete(self, obj):
        bucket = self.store.get(type(obj), [])
        if obj in bucket:
            bucket.remove(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        return None

    def rollback(self):
        return None

    def matches(self, row, clause) -> bool:
        try:
            key = clause.left.key
            right = clause.right
            expected = right.value if hasattr(right, "value") else right
            return getattr(row, key) == expected
        except Exception:
            return True


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _override_user(role: str, user_id: int = 1) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=user_id,
        role=role,
    )


def _override_db(session: FakeSession) -> None:
    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db


def _choices() -> list[dict]:
    return [
        {"key": "A", "text": "Choice A"},
        {"key": "B", "text": "Choice B"},
        {"key": "C", "text": "Choice C"},
        {"key": "D", "text": "Choice D"},
    ]


def _question_payload(order_index: int, **overrides) -> dict:
    slot = QUESTION_LAYOUT[order_index]
    payload = {
        "section": slot.section,
        "domain": slot.domain,
        "difficulty": slot.difficulty,
        "points": slot.points,
        "order_index": order_index,
        "question_text": f"Question {order_index}",
        "choices": _choices(),
        "correct_choice": "B",
        "explanation": f"Explanation {order_index}",
    }
    payload.update(overrides)
    return payload


def _seed_questions(db: FakeSession, count: int = 20) -> list[DiagnosticQuestion]:
    questions = []
    for order_index in range(1, count + 1):
        slot = QUESTION_LAYOUT[order_index]
        question = DiagnosticQuestion(
            section=slot.section,
            domain=slot.domain,
            difficulty=slot.difficulty,
            points=slot.points,
            order_index=order_index,
            question_text=f"Question {order_index}",
            question_image=None,
            choices=_choices(),
            correct_choice="B",
            explanation=f"Explanation {order_index}",
            created_at=_utcnow(),
            created_by_id=99,
        )
        db.add(question)
        questions.append(question)
    return questions


def test_create_attempt_returns_in_progress_id(client: TestClient):
    db = FakeSession()
    _seed_questions(db)
    _override_db(db)
    _override_user("student", user_id=7)

    response = client.post("/diagnostic/attempts")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "in_progress"
    assert isinstance(body["attempt_id"], int)
    assert body["attempt_id"] > 0
    stored = db.store[DiagnosticAttempt][0]
    assert stored.student_id == 7
    assert stored.status == "in_progress"


def test_attempt_questions_omit_answer_key(client: TestClient):
    db = FakeSession()
    _seed_questions(db)
    attempt = DiagnosticAttempt(
        student_id=7,
        started_at=_utcnow(),
        status="in_progress",
    )
    db.add(attempt)
    _override_db(db)
    _override_user("student", user_id=7)

    response = client.get(f"/diagnostic/attempts/{attempt.id}/questions")
    assert response.status_code == 200
    questions = response.json()
    assert len(questions) == 20
    for question in questions:
        assert "correct_choice" not in question
        assert "explanation" not in question
        assert "points" not in question
        assert "question_text" in question
        assert "choices" in question


def test_submit_answer_computes_is_correct_server_side(client: TestClient):
    db = FakeSession()
    questions = _seed_questions(db)
    attempt = DiagnosticAttempt(
        student_id=7,
        started_at=_utcnow(),
        status="in_progress",
    )
    db.add(attempt)
    _override_db(db)
    _override_user("student", user_id=7)

    response = client.post(
        f"/diagnostic/attempts/{attempt.id}/answers",
        json={
            "question_id": questions[0].id,
            "selected_choice": "A",
            "is_correct": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "is_correct" not in body
    assert body["selected_choice"] == "A"
    stored = db.store[DiagnosticAnswer][0]
    assert stored.is_correct is False


def test_student_cannot_access_another_students_attempt(client: TestClient):
    db = FakeSession()
    attempt = DiagnosticAttempt(
        student_id=2,
        started_at=_utcnow(),
        status="in_progress",
    )
    db.add(attempt)
    _override_db(db)
    _override_user("student", user_id=1)

    response = client.get(f"/diagnostic/attempts/{attempt.id}")
    assert response.status_code == 403


def test_complete_attempt_scores_known_answers(client: TestClient):
    db = FakeSession()
    questions = _seed_questions(db)
    attempt = DiagnosticAttempt(
        student_id=7,
        started_at=_utcnow(),
        status="in_progress",
    )
    db.add(attempt)
    for question in questions:
        if question.section == "reading_writing":
            db.add(
                DiagnosticAnswer(
                    attempt_id=attempt.id,
                    question_id=question.id,
                    selected_choice="B",
                    is_correct=True,
                    answered_at=_utcnow(),
                )
            )
    _override_db(db)
    _override_user("student", user_id=7)

    response = client.post(f"/diagnostic/attempts/{attempt.id}/complete")
    assert response.status_code == 200
    body = response.json()
    expected = estimate_result(50, 0)
    assert body["rw_points"] == 50
    assert body["math_points"] == 0
    assert body["status"] == "completed"
    assert body["rw_scaled_estimate"] == expected["rw_scaled_estimate"]
    assert body["math_scaled_estimate"] == expected["math_scaled_estimate"]
    assert body["total_point_estimate"] == expected["total_point_estimate"]
    assert body["total_range_low"] == expected["total_range_low"]
    assert body["total_range_high"] == expected["total_range_high"]


def test_complete_attempt_twice_is_rejected(client: TestClient):
    db = FakeSession()
    _seed_questions(db)
    attempt = DiagnosticAttempt(
        student_id=7,
        started_at=_utcnow(),
        status="in_progress",
        rw_points=None,
        math_points=None,
    )
    db.add(attempt)
    _override_db(db)
    _override_user("student", user_id=7)

    first = client.post(f"/diagnostic/attempts/{attempt.id}/complete")
    assert first.status_code == 200
    first_total = first.json()["total_point_estimate"]

    second = client.post(f"/diagnostic/attempts/{attempt.id}/complete")
    assert second.status_code == 409
    assert attempt.total_point_estimate == first_total


def test_question_bank_roles(client: TestClient):
    db = FakeSession()
    _override_db(db)

    payload = _question_payload(1)

    _override_user("student", user_id=3)
    assert client.get("/diagnostic/questions").status_code == 403
    assert client.post("/diagnostic/questions", json=payload).status_code == 403

    _override_user("teacher", user_id=4)
    assert client.get("/diagnostic/questions").status_code == 200
    assert client.post("/diagnostic/questions", json=payload).status_code == 403
    assert client.put("/diagnostic/questions/1", json=payload).status_code == 403
    assert client.delete("/diagnostic/questions/1").status_code == 403

    _override_user("mentor", user_id=5)
    created = client.post("/diagnostic/questions", json=payload)
    assert created.status_code == 200
    question_id = created.json()["id"]
    updated = client.put(
        f"/diagnostic/questions/{question_id}",
        json=_question_payload(1, question_text="Updated stem"),
    )
    assert updated.status_code == 200
    assert updated.json()["question_text"] == "Updated stem"
    assert updated.json()["correct_choice"] == "B"

    _override_user("admin", user_id=6)
    listed = client.get("/diagnostic/questions")
    assert listed.status_code == 200
    assert listed.json()[0]["correct_choice"] == "B"
    deleted = client.delete(f"/diagnostic/questions/{question_id}")
    assert deleted.status_code == 200


def test_question_layout_validation_rejects_mismatch(client: TestClient):
    db = FakeSession()
    _override_db(db)
    _override_user("admin", user_id=1)

    payload = _question_payload(3, difficulty="hard", points=3)
    response = client.post("/diagnostic/questions", json=payload)
    assert response.status_code == 422


def test_delete_question_blocked_when_answers_exist(client: TestClient):
    db = FakeSession()
    questions = _seed_questions(db, count=1)
    attempt = DiagnosticAttempt(
        student_id=7,
        started_at=_utcnow(),
        status="in_progress",
    )
    db.add(attempt)
    db.add(
        DiagnosticAnswer(
            attempt_id=attempt.id,
            question_id=questions[0].id,
            selected_choice="B",
            is_correct=True,
            answered_at=_utcnow(),
        )
    )
    _override_db(db)
    _override_user("admin", user_id=1)

    response = client.delete(f"/diagnostic/questions/{questions[0].id}")
    assert response.status_code == 409


def test_save_progress_sets_math_start_once_and_updates_question(client: TestClient):
    db = FakeSession()
    questions = _seed_questions(db)
    attempt = DiagnosticAttempt(
        student_id=7,
        started_at=_utcnow(),
        status="in_progress",
    )
    db.add(attempt)
    _override_db(db)
    _override_user("student", user_id=7)

    first_start = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    first = client.patch(
        f"/diagnostic/attempts/{attempt.id}/progress",
        json={
            "current_question_id": questions[10].id,
            "math_started_at": first_start.isoformat(),
        },
    )
    assert first.status_code == 200
    assert first.json()["current_question_id"] == questions[10].id
    assert attempt.math_started_at == first_start

    later_start = datetime(2026, 8, 17, 9, 20, tzinfo=timezone.utc)
    second = client.patch(
        f"/diagnostic/attempts/{attempt.id}/progress",
        json={
            "current_question_id": questions[12].id,
            "math_started_at": later_start.isoformat(),
        },
    )
    assert second.status_code == 200
    assert second.json()["current_question_id"] == questions[12].id
    assert attempt.math_started_at == first_start
    assert attempt.current_question_id == questions[12].id


def test_save_progress_rejected_when_not_in_progress(client: TestClient):
    db = FakeSession()
    questions = _seed_questions(db, count=1)
    attempt = DiagnosticAttempt(
        student_id=7,
        started_at=_utcnow(),
        status="completed",
    )
    db.add(attempt)
    _override_db(db)
    _override_user("student", user_id=7)

    response = client.patch(
        f"/diagnostic/attempts/{attempt.id}/progress",
        json={"current_question_id": questions[0].id},
    )
    assert response.status_code == 409
