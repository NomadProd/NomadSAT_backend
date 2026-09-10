from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from dependencies.auth import AuthUser, get_current_user
from main import app
from Methods.auth import get_db
from models import (
    Class,
    ClassEnrollment,
    PracticeTest,
    PracticeTestClass,
    PracticeTestModule,
    PracticeTestQuestion,
)
from services.practice_test_config import MODULE_FORMAT
from tests.fake_session import FakeSession as _BaseFakeSession


class FakeSession(_BaseFakeSession):
    def __init__(self):
        super().__init__(
            models=(
                PracticeTest,
                PracticeTestModule,
                PracticeTestQuestion,
                PracticeTestClass,
                Class,
                ClassEnrollment,
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


def _as(role: str, user_id: int = 1) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthUser(id=user_id, role=role)


def _use(db: FakeSession) -> None:
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db


def _choices() -> list[dict]:
    return [{"key": k, "text": f"Choice {k}"} for k in "ABCD"]


def _mcq(order_index: int, **overrides) -> dict:
    payload = {
        "order_index": order_index,
        "domain": "Algebra",
        "difficulty": "easy",
        "question_text": f"Question {order_index}",
        "answer_type": "mcq",
        "choices": _choices(),
        "correct_choice": "B",
        "explanation": "Because.",
    }
    payload.update(overrides)
    return payload


def _seed_test(db: FakeSession, *, visible: bool = False) -> PracticeTest:
    test = PracticeTest(title="Practice Test 1", description=None, visible=visible)
    db.add(test)
    for spec in MODULE_FORMAT:
        db.add(
            PracticeTestModule(
                test_id=test.id,
                section=spec.section,
                order_index=spec.order_index,
                time_limit_seconds=spec.time_limit_seconds,
                required_question_count=spec.required_question_count,
            )
        )
    return test


def _modules(db: FakeSession, test_id: int) -> list[PracticeTestModule]:
    rows = [m for m in db.store[PracticeTestModule] if m.test_id == test_id]
    return sorted(rows, key=lambda m: m.order_index)


def _rw1(db: FakeSession, test_id: int) -> PracticeTestModule:
    return _modules(db, test_id)[0]


def _math1(db: FakeSession, test_id: int) -> PracticeTestModule:
    return _modules(db, test_id)[2]


def _fill_every_module(db: FakeSession, test_id: int, *, short: int = 0) -> None:
    """Author every module in full, optionally leaving the last one `short`."""
    modules = _modules(db, test_id)
    for index, module in enumerate(modules):
        missing = short if index == len(modules) - 1 else 0
        _fill_module(db, module, module.required_question_count - missing)


def _fill_module(db: FakeSession, module: PracticeTestModule, count: int) -> None:
    for order_index in range(1, count + 1):
        db.add(
            PracticeTestQuestion(
                module_id=module.id,
                order_index=order_index,
                domain="Algebra",
                difficulty="easy",
                question_text=f"Q{order_index}",
                answer_type="mcq",
                choices=_choices(),
                correct_choice="B",
                image_scale=0.85,
            )
        )


# --- creation ---------------------------------------------------------------


def test_teacher_can_create_a_test_with_the_official_module_format(client: TestClient):
    db = FakeSession()
    _use(db)
    _as("teacher")

    response = client.post("/practice-tests", json={"title": "  Mock 1  "})

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Mock 1"
    assert body["visible"] is False
    assert body["is_publishable"] is False
    assert [(m["name"], m["required_question_count"], m["time_limit_seconds"])
            for m in body["modules"]] == [
        ("Reading & Writing 1", 27, 1920),
        ("Reading & Writing 2", 27, 1920),
        ("Math 1", 22, 2100),
        ("Math 2", 22, 2100),
    ]
    assert all(m["question_count"] == 0 for m in body["modules"])


def test_title_is_required(client: TestClient):
    db = FakeSession()
    _use(db)
    _as("teacher")

    assert client.post("/practice-tests", json={"title": "   "}).status_code == 422


# --- authoring --------------------------------------------------------------


def test_teacher_can_author_a_question(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    rw = _rw1(db, test.id)
    _use(db)
    _as("teacher")

    response = client.post(
        f"/practice-tests/modules/{rw.id}/questions", json=_mcq(1)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["module_id"] == rw.id
    assert body["answer_type"] == "mcq"
    assert body["correct_choice"] == "B"
    assert body["correct_answers"] is None


def test_grid_in_question_is_stored_with_its_accepted_answers(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    math = _math1(db, test.id)
    _use(db)
    _as("teacher")

    response = client.post(
        f"/practice-tests/modules/{math.id}/questions",
        json=_mcq(
            1,
            answer_type="spr",
            choices=None,
            correct_choice=None,
            correct_answers=["1/2", "0.5"],
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correct_answers"] == ["1/2", "0.5"]
    assert body["choices"] is None


def test_reading_writing_module_rejects_grid_in(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    rw = _rw1(db, test.id)
    _use(db)
    _as("teacher")

    response = client.post(
        f"/practice-tests/modules/{rw.id}/questions",
        json=_mcq(1, answer_type="spr", choices=None, correct_choice=None,
                  correct_answers=["1/2"]),
    )

    assert response.status_code == 422
    assert "math module" in response.json()["detail"]


def test_grid_in_answer_that_cannot_be_typed_is_rejected(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    math = _math1(db, test.id)
    _use(db)
    _as("teacher")

    response = client.post(
        f"/practice-tests/modules/{math.id}/questions",
        json=_mcq(1, answer_type="spr", choices=None, correct_choice=None,
                  correct_answers=["3 1/2"]),
    )

    assert response.status_code == 422


def test_mcq_without_choices_is_rejected(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    rw = _rw1(db, test.id)
    _use(db)
    _as("teacher")

    response = client.post(
        f"/practice-tests/modules/{rw.id}/questions",
        json=_mcq(1, choices=None),
    )

    assert response.status_code == 422


def test_order_index_beyond_the_module_length_is_rejected(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    rw = _rw1(db, test.id)
    _use(db)
    _as("teacher")

    response = client.post(
        f"/practice-tests/modules/{rw.id}/questions", json=_mcq(28)
    )

    assert response.status_code == 422
    assert "between 1 and 27" in response.json()["detail"]


def test_duplicate_order_index_in_the_same_module_conflicts(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    rw, math = _rw1(db, test.id), _math1(db, test.id)
    _use(db)
    _as("teacher")

    assert client.post(f"/practice-tests/modules/{rw.id}/questions",
                       json=_mcq(1)).status_code == 200
    second = client.post(f"/practice-tests/modules/{rw.id}/questions", json=_mcq(1))
    assert second.status_code == 409

    # ...but the same position in the other module is fine.
    assert client.post(f"/practice-tests/modules/{math.id}/questions",
                       json=_mcq(1)).status_code == 200


def test_deleting_a_question_frees_its_slot(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    rw = _rw1(db, test.id)
    _use(db)
    _as("teacher")

    created = client.post(f"/practice-tests/modules/{rw.id}/questions",
                          json=_mcq(1)).json()
    assert client.delete(f"/practice-tests/questions/{created['id']}").status_code == 200
    assert client.post(f"/practice-tests/modules/{rw.id}/questions",
                       json=_mcq(1)).status_code == 200


def test_deleting_a_question_unpublishes_a_live_test(client: TestClient):
    db = FakeSession()
    test = _seed_test(db, visible=True)
    _fill_every_module(db, test.id)
    _use(db)
    _as("teacher")

    victim = db.store[PracticeTestQuestion][-1]
    response = client.delete(f"/practice-tests/questions/{victim.id}")

    assert response.status_code == 200
    assert response.json()["test_unpublished"] is True
    assert test.visible is False


def test_deleting_a_question_from_a_draft_changes_nothing_else(client: TestClient):
    db = FakeSession()
    test = _seed_test(db, visible=False)
    _fill_module(db, _rw1(db, test.id), 27)
    _use(db)
    _as("teacher")

    victim = db.store[PracticeTestQuestion][-1]
    response = client.delete(f"/practice-tests/questions/{victim.id}")

    assert response.json()["test_unpublished"] is False
    assert test.visible is False


def test_a_published_test_survives_an_edit_that_keeps_it_complete(client: TestClient):
    db = FakeSession()
    test = _seed_test(db, visible=True)
    _fill_every_module(db, test.id)
    _use(db)
    _as("teacher")

    existing = db.store[PracticeTestQuestion][0]
    response = client.put(
        f"/practice-tests/questions/{existing.id}",
        json=_mcq(existing.order_index, question_text="Reworded stem"),
    )

    assert response.status_code == 200
    assert test.visible is True


# --- publishing -------------------------------------------------------------


def test_publishing_an_incomplete_module_conflicts(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    _fill_every_module(db, test.id, short=1)
    _use(db)
    _as("teacher")

    response = client.patch(f"/practice-tests/{test.id}/visible",
                            json={"visible": True})

    assert response.status_code == 409
    assert "The Math 2 module has 21 of 22 questions" == response.json()["detail"]
    assert test.visible is False


def test_publishing_a_complete_test_succeeds(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    _fill_every_module(db, test.id)
    _use(db)
    _as("teacher")

    response = client.patch(f"/practice-tests/{test.id}/visible",
                            json={"visible": True})

    assert response.status_code == 200
    assert response.json()["visible"] is True
    assert response.json()["is_publishable"] is True


def test_hiding_a_test_never_checks_completeness(client: TestClient):
    db = FakeSession()
    test = _seed_test(db, visible=True)
    _use(db)
    _as("teacher")

    response = client.patch(f"/practice-tests/{test.id}/visible",
                            json={"visible": False})

    assert response.status_code == 200
    assert response.json()["visible"] is False


# --- class assignment -------------------------------------------------------


def test_teacher_cannot_assign_a_class_they_do_not_own(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    db.add(Class(id=1, name="Mine", archived=False, verbal_teacher_id=7))
    db.add(Class(id=2, name="Theirs", archived=False, verbal_teacher_id=99))
    _use(db)
    _as("teacher", user_id=7)

    assert client.put(f"/practice-tests/{test.id}/classes",
                      json={"class_ids": [1]}).status_code == 200
    denied = client.put(f"/practice-tests/{test.id}/classes",
                        json={"class_ids": [1, 2]})
    assert denied.status_code == 400
    assert "[2]" in denied.json()["detail"]


def test_admin_can_assign_any_class_and_assignment_replaces(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    db.add(Class(id=1, name="A", archived=False))
    db.add(Class(id=2, name="B", archived=False))
    _use(db)
    _as("admin")

    first = client.put(f"/practice-tests/{test.id}/classes", json={"class_ids": [1, 2]})
    assert first.json()["class_ids"] == [1, 2]

    second = client.put(f"/practice-tests/{test.id}/classes", json={"class_ids": [2]})
    assert second.json()["class_ids"] == [2]


# --- student visibility -----------------------------------------------------


def test_student_sees_only_visible_tests_for_their_classes(client: TestClient):
    db = FakeSession()
    mine = _seed_test(db, visible=True)
    hidden = _seed_test(db, visible=False)
    other = _seed_test(db, visible=True)
    db.add(PracticeTestClass(test_id=mine.id, class_id=1))
    db.add(PracticeTestClass(test_id=hidden.id, class_id=1))
    db.add(PracticeTestClass(test_id=other.id, class_id=2))
    db.add(ClassEnrollment(class_id=1, student_id=42))
    _use(db)
    _as("student", user_id=42)

    body = client.get("/practice-tests").json()

    assert [item["id"] for item in body] == [mine.id]
    assert client.get(f"/practice-tests/{hidden.id}").status_code == 404
    assert client.get(f"/practice-tests/{other.id}").status_code == 404
    assert client.get(f"/practice-tests/{mine.id}").status_code == 200


def test_student_with_no_classes_sees_nothing(client: TestClient):
    db = FakeSession()
    _seed_test(db, visible=True)
    _use(db)
    _as("student", user_id=42)

    assert client.get("/practice-tests").json() == []


def test_staff_see_hidden_and_unassigned_tests(client: TestClient):
    db = FakeSession()
    _seed_test(db, visible=False)
    _use(db)
    _as("teacher")

    assert len(client.get("/practice-tests").json()) == 1


def test_deleted_tests_disappear(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    _use(db)
    _as("teacher")

    assert client.delete(f"/practice-tests/{test.id}").status_code == 200
    assert client.get("/practice-tests").json() == []
    assert client.get(f"/practice-tests/{test.id}").status_code == 404


# --- roles ------------------------------------------------------------------


def test_students_are_forbidden_from_every_authoring_endpoint(client: TestClient):
    db = FakeSession()
    test = _seed_test(db)
    rw = _rw1(db, test.id)
    _use(db)
    _as("student", user_id=42)

    calls = [
        ("post", "/practice-tests", {"title": "x"}),
        ("patch", f"/practice-tests/{test.id}", {"title": "x"}),
        ("delete", f"/practice-tests/{test.id}", None),
        ("put", f"/practice-tests/{test.id}/classes", {"class_ids": []}),
        ("patch", f"/practice-tests/{test.id}/visible", {"visible": True}),
        ("post", f"/practice-tests/modules/{rw.id}/questions", _mcq(1)),
        ("get", f"/practice-tests/modules/{rw.id}/questions", None),
        ("put", "/practice-tests/questions/1", _mcq(1)),
        ("delete", "/practice-tests/questions/1", None),
    ]
    for method, url, payload in calls:
        response = getattr(client, method)(url, **({"json": payload} if payload else {}))
        assert response.status_code == 403, f"{method.upper()} {url} -> {response.status_code}"


@pytest.mark.parametrize("role", ["teacher", "mentor", "admin"])
def test_every_staff_role_can_author(client: TestClient, role: str):
    db = FakeSession()
    test = _seed_test(db)
    rw = _rw1(db, test.id)
    _use(db)
    _as(role)

    assert client.post(
        f"/practice-tests/modules/{rw.id}/questions", json=_mcq(1)
    ).status_code == 200


# --- image upload -----------------------------------------------------------


def test_question_image_upload_returns_a_url(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "routes.practice_tests.upload_file",
        lambda *args, **kwargs: {"url": "https://cdn/x.png", "public_id": "practice_1_ab"},
    )
    _as("teacher")

    response = client.post(
        "/practice-tests/questions/image",
        files={"file": ("diagram.png", io.BytesIO(b"bytes"), "image/png")},
    )

    assert response.status_code == 200
    assert response.json() == {"url": "https://cdn/x.png", "public_id": "practice_1_ab"}


def test_question_image_upload_rejects_a_pdf(client: TestClient):
    _as("teacher")

    response = client.post(
        "/practice-tests/questions/image",
        files={"file": ("notes.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
    )

    assert response.status_code == 422
