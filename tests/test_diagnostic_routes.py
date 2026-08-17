from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from dependencies.auth import AuthUser, get_current_user
from main import app
from Methods.auth import get_db
from models import (
    Class,
    ClassEnrollment,
    DiagnosticAnswer,
    DiagnosticAttempt,
    DiagnosticQuestion,
    User,
)
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
            User: [],
            Class: [],
            ClassEnrollment: [],
        }
        self.counters = {
            DiagnosticQuestion: 1,
            DiagnosticAttempt: 1,
            DiagnosticAnswer: 1,
            User: 1,
            Class: 1,
            ClassEnrollment: 1,
        }
        self.committed = False

    def query(self, model):
        return _FakeQuery(self, model, self.store.get(model, []))

    def add(self, obj):
        model = type(obj)
        current_id = getattr(obj, "id", None)
        if current_id is None and model in self.counters:
            obj.id = self.counters[model]
            self.counters[model] += 1
        elif isinstance(current_id, int) and model in self.counters:
            self.counters[model] = max(self.counters[model], current_id + 1)
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
        clauses = getattr(clause, "clauses", None)
        operator = getattr(clause, "operator", None)
        if clauses is not None and operator is not None:
            parts = [self.matches(row, child) for child in clauses]
            op_name = getattr(operator, "__name__", str(operator))
            if op_name in ("or_", "or"):
                return any(parts)
            return all(parts)

        left = getattr(clause, "left", None)
        right = getattr(clause, "right", None)
        key = getattr(left, "key", None)
        if key is None:
            return False
        actual = getattr(row, key, None)
        expected = right.value if hasattr(right, "value") else right
        op_name = getattr(operator, "__name__", "")
        if operator is not None and (
            op_name in ("in_op", "in_") or getattr(operator, "__name__", "") == "in_op"
        ):
            values = expected
            if isinstance(values, (list, tuple, set)):
                return actual in values
            return False
        if op_name in ("is_", "is"):
            name = type(expected).__name__ if expected is not None else "NoneType"
            if expected is None or name == "Null":
                return actual is None
            if name == "True_":
                return actual is True
            if name == "False_":
                return actual is False
            expected_value = expected.value if hasattr(expected, "value") else expected
            return actual is expected_value
        return actual == expected


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
            passage_text=None,
            question_text=f"Question {order_index}",
            question_url=None,
            question_image=None,
            question_image_public_id=None,
            choices=_choices(),
            correct_choice="B",
            explanation=f"Explanation {order_index}",
            created_at=_utcnow(),
            created_by_id=99,
        )
        db.add(question)
        questions.append(question)
    return questions


def _seed_user(
    db: FakeSession,
    user_id: int,
    *,
    name: str = "Ada",
    surname: str = "Lovelace",
    role: str = "student",
) -> User:
    user = User(
        id=user_id,
        email=f"user{user_id}@turan.test",
        hashed_password="x",
        name=name,
        surname=surname,
        role=role,
    )
    db.add(user)
    return user


def _seed_class(
    db: FakeSession,
    class_id: int,
    *,
    verbal_teacher_id: int | None = None,
    math_teacher_id: int | None = None,
    archived: bool = False,
) -> Class:
    class_obj = Class(
        id=class_id,
        name=f"Class {class_id}",
        archived=archived,
        verbal_teacher_id=verbal_teacher_id,
        math_teacher_id=math_teacher_id,
    )
    db.add(class_obj)
    return class_obj


def _enroll(db: FakeSession, class_id: int, student_id: int) -> None:
    db.add(ClassEnrollment(class_id=class_id, student_id=student_id))


def _completed_attempt(
    db: FakeSession,
    student_id: int,
    *,
    completed_at: datetime | None = None,
    total_range_low: int = 1220,
    total_range_high: int = 1380,
) -> DiagnosticAttempt:
    finished = completed_at or _utcnow()
    attempt = DiagnosticAttempt(
        student_id=student_id,
        started_at=finished,
        completed_at=finished,
        status="completed",
        rw_points=30,
        math_points=40,
        rw_scaled_estimate=600,
        math_scaled_estimate=700,
        total_point_estimate=1300,
        total_range_low=total_range_low,
        total_range_high=total_range_high,
    )
    db.add(attempt)
    return attempt


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
    assert stored.question_ids == [question.id for question in db.store[DiagnosticQuestion]]
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
        assert "passage_text" in question
        assert "choices" in question
        assert "question_url" not in question
        assert "question_image_public_id" not in question
        assert "question_image" in question
    assert "image_scale" in question


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


def test_submit_answer_twice_updates_same_row(client: TestClient):
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

    first = client.post(
        f"/diagnostic/attempts/{attempt.id}/answers",
        json={"question_id": questions[0].id, "selected_choice": "A"},
    )
    second = client.post(
        f"/diagnostic/attempts/{attempt.id}/answers",
        json={"question_id": questions[0].id, "selected_choice": "C"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["selected_choice"] == "C"
    answers = db.store[DiagnosticAnswer]
    assert len(answers) == 1
    assert answers[0].selected_choice == "C"


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


def test_question_url_is_stored_and_must_be_unique(client: TestClient):
    db = FakeSession()
    _override_db(db)
    _override_user("admin", user_id=1)

    first = client.post(
        "/diagnostic/questions",
        json=_question_payload(1, question_url="dadada"),
    )
    assert first.status_code == 200
    assert first.json()["question_url"] == "dadada"
    assert db.store[DiagnosticQuestion][0].question_url == "dadada"

    duplicate = client.post(
        "/diagnostic/questions",
        json=_question_payload(2, question_url="dadada"),
    )
    assert duplicate.status_code == 409


def test_question_image_is_returned_to_students(client: TestClient):
    db = FakeSession()
    questions = _seed_questions(db, count=1)
    questions[0].question_image = "https://cdn.example.com/figure.png"
    questions[0].question_image_public_id = "diagnostic/abc"
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
    body = response.json()[0]
    assert body["question_image"] == "https://cdn.example.com/figure.png"
    assert body["image_scale"] == 0.85
    assert "question_image_public_id" not in body
    assert "question_url" not in body


def test_image_scale_is_persisted_and_returned_to_students(client: TestClient):
    db = FakeSession()
    _override_db(db)
    _override_user("admin", user_id=1)

    created = client.post(
        "/diagnostic/questions",
        json=_question_payload(
            1,
            question_image="https://cdn.example.com/figure.png",
            image_scale=0.6,
        ),
    )
    assert created.status_code == 200
    assert created.json()["image_scale"] == 0.6
    assert db.store[DiagnosticQuestion][0].image_scale == 0.6

    too_large = client.post(
        "/diagnostic/questions",
        json=_question_payload(2, image_scale=1.4),
    )
    assert too_large.status_code == 422

    attempt = DiagnosticAttempt(
        student_id=7,
        started_at=_utcnow(),
        status="in_progress",
    )
    db.add(attempt)
    _override_user("student", user_id=7)

    response = client.get(f"/diagnostic/attempts/{attempt.id}/questions")
    assert response.status_code == 200
    body = response.json()[0]
    assert body["image_scale"] == 0.6
    assert body["question_image"] == "https://cdn.example.com/figure.png"


def test_upload_question_image_returns_url(client: TestClient, monkeypatch):
    _override_user("admin", user_id=1)

    def fake_upload(file_bytes, **kwargs):
        assert kwargs["folder"] == "diagnostic_questions"
        assert kwargs["content_type"] == "image/png"
        return {
            "url": "https://res.cloudinary.com/demo/image/upload/q.png",
            "public_id": "diagnostic_questions/diagnostic_1_abcd",
            "filename": "figure.png",
            "content_type": "image/png",
            "size_bytes": len(file_bytes),
        }

    monkeypatch.setattr("routes.diagnostic.upload_file", fake_upload)
    response = client.post(
        "/diagnostic/questions/image",
        files={"file": ("figure.png", b"\x89PNG\r\n", "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["url"].startswith("https://")
    assert response.json()["public_id"]


def test_upload_question_image_rejects_pdf(client: TestClient, monkeypatch):
    _override_user("admin", user_id=1)
    called = []

    def fake_upload(*args, **kwargs):
        called.append(True)
        raise AssertionError("should not upload")

    monkeypatch.setattr("routes.diagnostic.upload_file", fake_upload)
    response = client.post(
        "/diagnostic/questions/image",
        files={"file": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 422
    assert called == []


def test_delete_question_with_answers_frees_slot(client: TestClient):
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
    assert response.status_code == 200
    assert questions[0].deleted_at is not None
    assert attempt.question_ids == [questions[0].id]

    listed = client.get("/diagnostic/questions")
    assert listed.status_code == 200
    assert listed.json() == []

    created = client.post(
        "/diagnostic/questions",
        json=_question_payload(1, question_text="Replacement stem"),
    )
    assert created.status_code == 200
    assert created.json()["id"] != questions[0].id
    assert created.json()["question_text"] == "Replacement stem"


def test_in_progress_attempt_keeps_deleted_question(client: TestClient):
    db = FakeSession()
    questions = _seed_questions(db)
    _override_db(db)
    _override_user("student", user_id=7)

    started = client.post("/diagnostic/attempts")
    assert started.status_code == 200
    attempt_id = started.json()["attempt_id"]
    old_id = questions[0].id

    _override_user("admin", user_id=1)
    deleted = client.delete(f"/diagnostic/questions/{old_id}")
    assert deleted.status_code == 200
    replacement = client.post(
        "/diagnostic/questions",
        json=_question_payload(1, question_text="New stem"),
    )
    assert replacement.status_code == 200
    new_id = replacement.json()["id"]

    _override_user("student", user_id=7)
    body = client.get(f"/diagnostic/attempts/{attempt_id}/questions").json()
    old_item = next(item for item in body if item["order_index"] == 1)
    assert old_item["id"] == old_id
    assert old_item["question_text"] == "Question 1"

    started_new = client.post("/diagnostic/attempts")
    assert started_new.status_code == 200
    new_attempt_id = started_new.json()["attempt_id"]
    new_body = client.get(f"/diagnostic/attempts/{new_attempt_id}/questions").json()
    new_item = next(item for item in new_body if item["order_index"] == 1)
    assert new_item["id"] == new_id
    assert new_item["question_text"] == "New stem"


def test_completed_attempt_review_keeps_original_question(client: TestClient):
    db = FakeSession()
    _seed_user(db, 7)
    questions = _seed_questions(db)
    attempt = _completed_attempt(db, 7)
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

    deleted = client.delete(f"/diagnostic/questions/{questions[0].id}")
    assert deleted.status_code == 200
    replacement = client.post(
        "/diagnostic/questions",
        json=_question_payload(1, question_text="Replacement stem"),
    )
    assert replacement.status_code == 200

    _override_user("student", user_id=7)
    detail = client.get(f"/diagnostic/attempts/{attempt.id}/detail")
    assert detail.status_code == 200
    first = detail.json()["answers"][0]
    assert first["question_text"] == "Question 1"
    assert first["correct_choice"] == "B"
    assert first["selected_choice"] == "B"


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


def test_student_me_returns_only_own_attempts(client: TestClient):
    db = FakeSession()
    _seed_user(db, 7)
    _seed_user(db, 8, name="Other", surname="Student")
    mine_new = _completed_attempt(
        db,
        7,
        completed_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        total_range_low=1280,
        total_range_high=1440,
    )
    mine_old = _completed_attempt(
        db,
        7,
        completed_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        total_range_low=1200,
        total_range_high=1360,
    )
    _completed_attempt(db, 8)
    _override_db(db)
    _override_user("student", user_id=7)

    response = client.get("/diagnostic/attempts/me")
    assert response.status_code == 200
    body = response.json()
    assert [item["attempt_id"] for item in body] == [mine_new.id, mine_old.id]
    assert all(item["student_id"] == 7 for item in body)
    assert body[0]["total_range_low"] == 1280
    assert body[0]["total_range_high"] == 1440


def test_student_cannot_fetch_another_students_attempt_detail(client: TestClient):
    db = FakeSession()
    _seed_user(db, 7)
    _seed_user(db, 8, name="Other", surname="Student")
    questions = _seed_questions(db, count=1)
    attempt = _completed_attempt(db, 8)
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
    _override_user("student", user_id=7)

    response = client.get(f"/diagnostic/attempts/{attempt.id}/detail")
    assert response.status_code == 403


def test_student_own_completed_detail_includes_answer_key(client: TestClient):
    db = FakeSession()
    _seed_user(db, 7)
    questions = _seed_questions(db, count=2)
    attempt = _completed_attempt(db, 7)
    db.add(
        DiagnosticAnswer(
            attempt_id=attempt.id,
            question_id=questions[0].id,
            selected_choice="A",
            is_correct=False,
            answered_at=_utcnow(),
        )
    )
    _override_db(db)
    _override_user("student", user_id=7)

    response = client.get(f"/diagnostic/attempts/{attempt.id}/detail")
    assert response.status_code == 200
    body = response.json()
    assert body["attempt_id"] == attempt.id
    answers = body["answers"]
    assert len(answers) == 2
    first = answers[0]
    assert first["selected_choice"] == "A"
    assert first["correct_choice"] == "B"
    assert first["is_correct"] is False
    assert first["explanation"] == "Explanation 1"
    unanswered = answers[1]
    assert unanswered["selected_choice"] is None
    assert unanswered["is_correct"] is None
    assert unanswered["correct_choice"] == "B"


def test_in_progress_attempt_detail_returns_conflict(client: TestClient):
    db = FakeSession()
    _seed_user(db, 7)
    _seed_questions(db, count=1)
    attempt = DiagnosticAttempt(
        student_id=7,
        started_at=_utcnow(),
        status="in_progress",
    )
    db.add(attempt)
    _override_db(db)
    _override_user("student", user_id=7)

    response = client.get(f"/diagnostic/attempts/{attempt.id}/detail")
    assert response.status_code == 409
    assert "completed" in response.json()["detail"].lower()


def test_teacher_can_fetch_detail_for_student_in_own_class(client: TestClient):
    db = FakeSession()
    _seed_user(db, 7)
    _seed_user(db, 10, name="Pat", surname="Teacher", role="teacher")
    _seed_class(db, 1, verbal_teacher_id=10)
    _enroll(db, 1, 7)
    questions = _seed_questions(db, count=1)
    attempt = _completed_attempt(db, 7)
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
    _override_user("teacher", user_id=10)

    response = client.get(f"/diagnostic/attempts/{attempt.id}/detail")
    assert response.status_code == 200
    body = response.json()
    assert body["student"]["id"] == 7
    assert body["answers"][0]["correct_choice"] == "B"


def test_teacher_forbidden_for_student_outside_own_class(client: TestClient):
    db = FakeSession()
    _seed_user(db, 7)
    _seed_user(db, 8, name="Other", surname="Student")
    _seed_user(db, 10, name="Pat", surname="Teacher", role="teacher")
    _seed_class(db, 1, verbal_teacher_id=10)
    _seed_class(db, 2, verbal_teacher_id=99)
    _enroll(db, 1, 7)
    _enroll(db, 2, 8)
    _seed_questions(db, count=1)
    attempt = _completed_attempt(db, 8)
    _override_db(db)
    _override_user("teacher", user_id=10)

    response = client.get(f"/diagnostic/attempts/{attempt.id}/detail")
    assert response.status_code == 403


def test_mentor_and_admin_can_fetch_any_student_detail(client: TestClient):
    db = FakeSession()
    _seed_user(db, 8, name="Other", surname="Student")
    questions = _seed_questions(db, count=1)
    attempt = _completed_attempt(db, 8)
    db.add(
        DiagnosticAnswer(
            attempt_id=attempt.id,
            question_id=questions[0].id,
            selected_choice="C",
            is_correct=False,
            answered_at=_utcnow(),
        )
    )
    _override_db(db)

    _override_user("mentor", user_id=3)
    mentor = client.get(f"/diagnostic/attempts/{attempt.id}/detail")
    assert mentor.status_code == 200
    assert mentor.json()["student"]["id"] == 8

    _override_user("admin", user_id=1)
    admin = client.get(f"/diagnostic/attempts/{attempt.id}/detail")
    assert admin.status_code == 200
    assert admin.json()["answers"][0]["correct_choice"] == "B"


def test_class_attempts_are_scoped_by_role(client: TestClient):
    db = FakeSession()
    _seed_user(db, 7, name="In", surname="Class")
    _seed_user(db, 8, name="Out", surname="Class")
    _seed_user(db, 10, name="Pat", surname="Teacher", role="teacher")
    _seed_class(db, 1, verbal_teacher_id=10)
    _seed_class(db, 2, math_teacher_id=99)
    _enroll(db, 1, 7)
    _enroll(db, 2, 8)
    in_class = _completed_attempt(db, 7)
    _completed_attempt(db, 8)
    _override_db(db)

    _override_user("teacher", user_id=10)
    teacher = client.get("/diagnostic/attempts", params={"class_id": 1})
    assert teacher.status_code == 200
    teacher_ids = [item["attempt_id"] for item in teacher.json()]
    assert teacher_ids == [in_class.id]
    assert teacher.json()[0]["student"]["id"] == 7

    outsider = client.get("/diagnostic/attempts", params={"class_id": 2})
    assert outsider.status_code == 403

    _override_user("admin", user_id=1)
    admin = client.get("/diagnostic/attempts", params={"class_id": 2})
    assert admin.status_code == 200
    assert {item["student_id"] for item in admin.json()} == {8}
