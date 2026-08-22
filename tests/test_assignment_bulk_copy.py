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
from routers.assignments_router import copy_assignment
from schemas import CopyAssignmentData


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class _BulkFakeQuery:
    def __init__(self, session: "_BulkFakeSession", model, rows):
        self.session = session
        self.model = model
        self.rows = list(rows)

    def filter(self, *clauses):
        rows = self.rows
        for clause in clauses:
            rows = [row for row in rows if self.session.matches(row, clause)]
        return _BulkFakeQuery(self.session, self.model, rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class _BulkFakeSession:
    def __init__(self, store: dict):
        self.store = {model: list(rows) for model, rows in store.items()}
        self.added: list[Assignment] = []
        self.committed = False
        self._next_id = 5000

    def query(self, model):
        return _BulkFakeQuery(self, model, self.store.get(model, []))

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1
        bucket = self.store.setdefault(type(obj), [])
        if obj not in bucket:
            bucket.append(obj)

    def flush(self):
        return None

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        return None

    def matches(self, row, clause) -> bool:
        left = getattr(clause, "left", None)
        right = getattr(clause, "right", None)
        key = getattr(left, "key", None)
        if key is None:
            return True
        actual = getattr(row, key, None)
        expected = right.value if hasattr(right, "value") else right
        op_name = getattr(getattr(clause, "operator", None), "__name__", "")
        if op_name == "is_":
            if expected is True:
                return actual is True
            if expected is False:
                return actual is False
            return actual is None
        if op_name == "in_op":
            return actual in expected
        if op_name == "eq":
            return actual == expected
        return actual == expected


def _user(user_id: int, role: str = "student"):
    return SimpleNamespace(id=user_id, role=role)


def _assignment(**kwargs):
    defaults = dict(
        id=41,
        session_id=7,
        student_id=501,
        slot_index=1,
        title="Bulk source",
        instruction="read chapter 1",
        task_link="https://example.com/task",
        due_date=dt.date(2026, 8, 1),
        due_time=dt.time(10, 0),
        photo_required=True,
        homework_document={
            "url": "https://res.cloudinary.com/demo/raw/upload/doc.pdf",
            "public_id": "homework_documents/assignment_41_abcd",
            "filename": "doc.pdf",
        },
        homework_result=SimpleNamespace(id=999, submitted=True),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _session(**kwargs):
    defaults = dict(id=7, class_id=12, teacher_id=700)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _class(**kwargs):
    defaults = dict(
        id=12,
        verbal_teacher_id=700,
        math_teacher_id=701,
        archived=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _enrollment(class_id: int, student_id: int):
    return SimpleNamespace(class_id=class_id, student_id=student_id)


def _build_same_session_db(*, source_student_id: int = 501, extra_student_ids: list[int]):
    source = _assignment(student_id=source_student_id)
    session = _session(id=7, class_id=12, teacher_id=700)
    class_obj = _class(id=12)
    enrollments = [_enrollment(12, source_student_id)]
    users = [_user(source_student_id, "student")]
    for student_id in extra_student_ids:
        enrollments.append(_enrollment(12, student_id))
        users.append(_user(student_id, "student"))
    return _BulkFakeSession(
        {
            Assignment: [source],
            ClassSession: [session],
            Class: [class_obj],
            ClassEnrollment: enrollments,
            User: users,
        }
    )


def test_admin_bulk_copy_same_session_creates_for_all_targets():
    db = _build_same_session_db(extra_student_ids=[502, 503, 504, 505, 506])
    source = db.store[Assignment][0]

    response = copy_assignment(
        assignment_id=source.id,
        data=CopyAssignmentData(target_student_ids=[502, 503, 504, 505, 506]),
        db=db,
        current_user=_user(1, "admin"),
    )

    assert len(response["created"]) == 5
    assert response["skipped"] == []
    assert len(db.added) == 5
    assert source.title == "Bulk source"
    assert source.homework_result is not None
    for created in db.added:
        assert created.session_id == 7
        assert created.instruction == source.instruction
        assert created.homework_document == source.homework_document
        assert getattr(created, "homework_result", None) is None
        assert created.id != source.id
    assert db.committed is True


def test_bulk_copy_deduplicates_duplicate_student_ids():
    db = _build_same_session_db(extra_student_ids=[502])
    source = db.store[Assignment][0]

    response = copy_assignment(
        assignment_id=source.id,
        data=CopyAssignmentData(target_student_ids=[502, 502, 502]),
        db=db,
        current_user=_user(1, "admin"),
    )

    assert len(response["created"]) == 1
    assert len(db.added) == 1


def test_empty_target_student_ids_same_session_returns_422():
    db = _build_same_session_db(extra_student_ids=[502])
    source = db.store[Assignment][0]

    with pytest.raises(HTTPException) as exc:
        copy_assignment(
            assignment_id=source.id,
            data=CopyAssignmentData(target_student_ids=[]),
            db=db,
            current_user=_user(1, "admin"),
        )
    assert exc.value.status_code == 422
    assert db.added == []


def test_teacher_rejects_entire_bulk_request_when_one_target_not_in_class():
    db = _build_same_session_db(extra_student_ids=[502, 503])
    source = db.store[Assignment][0]

    with pytest.raises(HTTPException) as exc:
        copy_assignment(
            assignment_id=source.id,
            data=CopyAssignmentData(target_student_ids=[502, 999]),
            db=db,
            current_user=_user(700, "teacher"),
        )
    assert exc.value.status_code == 403
    assert db.added == []


def test_teacher_bulk_copy_succeeds_for_all_class_students():
    db = _build_same_session_db(extra_student_ids=[502, 503])
    source = db.store[Assignment][0]

    response = copy_assignment(
        assignment_id=source.id,
        data=CopyAssignmentData(target_student_ids=[502, 503]),
        db=db,
        current_user=_user(700, "teacher"),
    )

    assert len(response["created"]) == 2
    assert db.added != []


def test_student_cannot_call_bulk_copy_endpoint(client: TestClient):
    app.dependency_overrides[methods_get_current_user] = lambda: _user(400, "student")

    def override_db():
        yield _BulkFakeSession({})

    app.dependency_overrides[get_db] = override_db

    response = client.post(
        "/assignments/41/copy",
        json={"target_student_ids": [502, 503]},
    )
    assert response.status_code == 403


def test_bulk_copy_reuses_homework_document_reference_without_reupload():
    db = _build_same_session_db(extra_student_ids=[502, 503])
    source = db.store[Assignment][0]
    public_id = source.homework_document["public_id"]

    copy_assignment(
        assignment_id=source.id,
        data=CopyAssignmentData(target_student_ids=[502, 503]),
        db=db,
        current_user=_user(1, "admin"),
    )

    assert len(db.added) == 2
    assert db.added[0].homework_document["public_id"] == public_id
    assert db.added[1].homework_document["public_id"] == public_id
