from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from Methods.auth import get_current_user as methods_get_current_user
from Methods.auth import get_db
from main import app
from models import Assignment, Class, ClassEnrollment, Session as ClassSession, User
from routers.assignments_router import copy_assignment, update_assignment
from schemas import CopyAssignmentData, UpdateAssignmentData


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class _FakeQuery:
    def __init__(self, payload):
        self.payload = payload

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        if isinstance(self.payload, list):
            return self.payload[0] if self.payload else None
        return self.payload

    def all(self):
        if self.payload is None:
            return []
        if isinstance(self.payload, list):
            return self.payload
        return [self.payload]


class _FakeSession:
    def __init__(self, model_payloads):
        self.model_payloads = {
            model: (payloads[:] if isinstance(payloads, list) else [payloads])
            for model, payloads in model_payloads.items()
        }
        self.added = []
        self.committed = False
        self.refreshed = []

    def query(self, model):
        queue = self.model_payloads.get(model, [None])
        payload = queue.pop(0) if queue else None
        self.model_payloads[model] = queue
        return _FakeQuery(payload)

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = len(self.added) + 1000

    def flush(self):
        return None

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        self.refreshed.append(obj)


def _user(role: str, user_id: int):
    return SimpleNamespace(id=user_id, role=role)


def _assignment(**kwargs):
    defaults = dict(
        id=41,
        session_id=7,
        student_id=501,
        slot_index=1,
        title="Old homework",
        instruction="old",
        task_link="http://old",
        due_date=dt.date(2026, 8, 1),
        due_time=dt.time(10, 0),
        photo_required=False,
        homework_document={"url": "https://x/doc.pdf", "filename": "doc.pdf"},
        homework_result=SimpleNamespace(
            id=999,
            submitted=True,
            submitted_at="2026-08-01T12:00:00Z",
        ),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _session(**kwargs):
    defaults = dict(id=7, class_id=12, teacher_id=200)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _class(**kwargs):
    defaults = dict(id=12, verbal_teacher_id=200, math_teacher_id=201, archived=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_admin_can_edit_past_due_assignment_and_keep_results_untouched():
    assignment = _assignment()
    existing_result = assignment.homework_result
    db = _FakeSession(
        {
            Assignment: assignment,
            ClassSession: _session(),
            Class: _class(),
        }
    )

    response = update_assignment(
        assignment_id=assignment.id,
        data=UpdateAssignmentData(
            title="Updated title",
            instruction="new instruction",
            due_date=dt.date(2025, 1, 1),
            due_time=dt.time(8, 30),
        ),
        db=db,
        current_user=_user("admin", 1),
    )

    assert response["session_id"] == assignment.session_id
    assert assignment.title == "Updated title"
    assert assignment.instruction == "new instruction"
    assert assignment.due_date == dt.date(2025, 1, 1)
    assert assignment.homework_result is existing_result
    assert db.committed is True


def test_mentor_can_edit_past_due_assignment():
    assignment = _assignment()
    db = _FakeSession(
        {
            Assignment: assignment,
            ClassSession: _session(),
            Class: _class(),
        }
    )

    response = update_assignment(
        assignment_id=assignment.id,
        data=UpdateAssignmentData(task_link="https://new.link"),
        db=db,
        current_user=_user("mentor", 2),
    )

    assert response["assignment_id"] == assignment.id
    assert assignment.task_link == "https://new.link"


def test_teacher_past_due_edit_behavior_unchanged_allowed_when_class_teacher():
    assignment = _assignment()
    db = _FakeSession(
        {
            Assignment: assignment,
            ClassSession: _session(),
            Class: _class(verbal_teacher_id=200, math_teacher_id=201),
        }
    )

    response = update_assignment(
        assignment_id=assignment.id,
        data=UpdateAssignmentData(title="Teacher edit"),
        db=db,
        current_user=_user("teacher", 200),
    )

    assert response["assignment_id"] == assignment.id
    assert assignment.title == "Teacher edit"


def test_student_cannot_edit_assignment_endpoint(client: TestClient):
    app.dependency_overrides[methods_get_current_user] = lambda: _user("student", 300)
    def override_db():
        yield _FakeSession({})
    app.dependency_overrides[get_db] = override_db

    response = client.patch("/assignments/41", json={"title": "blocked"})
    assert response.status_code == 403


def test_admin_can_copy_assignment_to_other_session():
    source = _assignment(session_id=7, student_id=501)
    source_session = _session(id=7, class_id=12, teacher_id=200)
    target_session = _session(id=8, class_id=13, teacher_id=999)
    source_class = _class(id=12, verbal_teacher_id=200, math_teacher_id=201)
    target_class = _class(id=13, verbal_teacher_id=301, math_teacher_id=302, archived=False)
    target_student = SimpleNamespace(id=777, role="student")
    target_enrollment = SimpleNamespace(class_id=13, student_id=777)

    db = _FakeSession(
        {
            Assignment: [source],
            ClassSession: [source_session, target_session],
            Class: [source_class, target_class],
            ClassEnrollment: [target_enrollment],
        }
    )
    db.model_payloads[User] = [target_student]

    response = copy_assignment(
        assignment_id=source.id,
        data=CopyAssignmentData(
            session_id=8,
            student_id=777,
            due_date=dt.date(2026, 8, 30),
            due_time=dt.time(19, 0),
        ),
        db=db,
        current_user=_user("admin", 1),
    )

    assert len(response["created"]) == 1
    assert response["created"][0]["student_id"] == 777
    assert len(db.added) == 1
    created = db.added[0]
    assert created.session_id == 8
    assert created.student_id == 777
    assert created.due_date == dt.date(2026, 8, 30)
    assert created.due_time == dt.time(19, 0)
    assert created.homework_document == source.homework_document


def test_teacher_can_copy_own_assignment_to_own_session():
    source = _assignment(session_id=7, student_id=501)
    source_session = _session(id=7, class_id=12, teacher_id=700)
    target_session = _session(id=8, class_id=12, teacher_id=700)
    class_obj = _class(id=12, verbal_teacher_id=700, math_teacher_id=701)
    target_student = SimpleNamespace(id=777, role="student")
    target_enrollment = SimpleNamespace(class_id=12, student_id=777)

    db = _FakeSession(
        {
            Assignment: [source],
            ClassSession: [source_session, target_session],
            Class: [class_obj, class_obj],
            ClassEnrollment: [target_enrollment],
        }
    )
    db.model_payloads[User] = [target_student]

    response = copy_assignment(
        assignment_id=source.id,
        data=CopyAssignmentData(
            session_id=8,
            student_id=777,
            due_date=dt.date(2026, 9, 1),
            due_time=dt.time(18, 0),
        ),
        db=db,
        current_user=_user("teacher", 700),
    )
    assert response["created"][0]["student_id"] == 777


def test_teacher_cannot_copy_another_teachers_source_assignment():
    source = _assignment(session_id=7, student_id=501)
    source_session = _session(id=7, class_id=12, teacher_id=900)
    target_session = _session(id=8, class_id=12, teacher_id=700)
    class_obj = _class(id=12)
    db = _FakeSession(
        {
            Assignment: [source],
            ClassSession: [source_session, target_session],
            Class: [class_obj, class_obj],
        }
    )

    with pytest.raises(HTTPException) as exc:
        copy_assignment(
            assignment_id=source.id,
            data=CopyAssignmentData(
                session_id=8,
                student_id=777,
                due_date=dt.date(2026, 9, 1),
                due_time=dt.time(18, 0),
            ),
            db=db,
            current_user=_user("teacher", 700),
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_teacher_cannot_copy_into_another_teachers_target_session():
    source = _assignment(session_id=7, student_id=501)
    source_session = _session(id=7, class_id=12, teacher_id=700)
    target_session = _session(id=8, class_id=13, teacher_id=701)
    source_class = _class(id=12)
    target_class = _class(id=13)
    db = _FakeSession(
        {
            Assignment: [source],
            ClassSession: [source_session, target_session],
            Class: [source_class, target_class],
        }
    )

    with pytest.raises(HTTPException) as exc:
        copy_assignment(
            assignment_id=source.id,
            data=CopyAssignmentData(
                session_id=8,
                student_id=777,
                due_date=dt.date(2026, 9, 1),
                due_time=dt.time(18, 0),
            ),
            db=db,
            current_user=_user("teacher", 700),
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_student_cannot_copy_assignment_endpoint(client: TestClient):
    app.dependency_overrides[methods_get_current_user] = lambda: _user("student", 400)
    def override_db():
        yield _FakeSession({})
    app.dependency_overrides[get_db] = override_db

    response = client.post(
        "/assignments/41/copy",
        json={
            "session_id": 8,
            "student_id": 777,
            "due_date": "2026-09-01",
            "due_time": "18:00:00",
        },
    )
    assert response.status_code == 403
