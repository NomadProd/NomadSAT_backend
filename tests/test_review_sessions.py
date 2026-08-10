from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from Methods.auth import get_current_user as methods_get_current_user
from main import app
from models import Class, ClassEnrollment, Session as ClassSession, User
from routers.attendance_router import submit_or_update_attendance
from routers.sessions_router import create_session
from schemas import AttendanceBulkData, AttendanceRecordData, CreateSessionData


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def all(self):
        if self._result is None:
            return []
        if isinstance(self._result, list):
            return self._result
        return [self._result]


class _FakeSession:
    def __init__(self, by_model: dict):
        self.by_model = by_model
        self.added = []
        self.committed = False

    def query(self, model):
        return _FakeQuery(self.by_model.get(model))

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = 101

    def flush(self):
        return None

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        return None

    def delete(self, obj):
        return None


def _user(role: str, user_id: int = 1):
    return SimpleNamespace(id=user_id, role=role)


def _class_obj():
    return SimpleNamespace(
        id=10,
        verbal_teacher_id=2,
        math_teacher_id=3,
    )


def _db_with_class():
    return _FakeSession(
        {
            Class: _class_obj(),
            User: SimpleNamespace(id=2, role="teacher"),
        }
    )


def test_create_session_schema_requires_subject_for_review():
    with pytest.raises(ValidationError):
        CreateSessionData(
            date=date(2026, 8, 10),
            session_type="review",
        )


def test_create_session_schema_accepts_review_with_subject():
    data = CreateSessionData(
        date=date(2026, 8, 10),
        session_type="Review",
        subject="Math",
        start_time=time(10, 0),
        end_time=time(11, 30),
    )
    assert data.session_type == "review"
    assert data.subject == "math"


def test_admin_can_create_review_session():
    class_obj = _class_obj()
    db = _db_with_class()

    result = create_session(
        class_id=10,
        data=CreateSessionData(
            date=date(2026, 8, 10),
            session_type="review",
            subject="verbal",
            start_time=time(10, 0),
            end_time=time(11, 30),
        ),
        db=db,
        current_user=_user("admin"),
    )

    assert result["session_type"] == "review"
    assert result["subject"] == "verbal"
    assert db.committed is True
    assert len(db.added) == 1
    assert db.added[0].session_type == "review"
    assert db.added[0].subject == "verbal"
    assert db.added[0].teacher_id == class_obj.verbal_teacher_id


def test_mentor_can_create_review_session():
    class_obj = _class_obj()
    db = _db_with_class()

    result = create_session(
        class_id=10,
        data=CreateSessionData(
            date=date(2026, 8, 10),
            session_type="review",
            subject="math",
        ),
        db=db,
        current_user=_user("mentor"),
    )

    assert result["subject"] == "math"
    assert db.added[0].teacher_id == class_obj.math_teacher_id


def test_teacher_cannot_create_review_session():
    db = _db_with_class()

    with pytest.raises(HTTPException) as exc:
        create_session(
            class_id=10,
            data=CreateSessionData(
                date=date(2026, 8, 10),
                session_type="review",
                subject="math",
            ),
            db=db,
            current_user=_user("teacher", user_id=2),
        )

    assert exc.value.status_code == 403


def test_student_create_review_session_returns_403(client: TestClient):
    app.dependency_overrides[methods_get_current_user] = lambda: _user("student")

    response = client.post(
        "/classes/10/sessions",
        json={
            "date": "2026-08-10",
            "session_type": "review",
            "subject": "verbal",
            "start_time": "10:00:00",
            "end_time": "11:30:00",
        },
    )

    assert response.status_code == 403


def test_admin_can_mark_review_attendance():
    session_obj = SimpleNamespace(
        id=55,
        class_id=10,
        session_type="review",
        subject="math",
    )
    enrollment = SimpleNamespace(class_id=10, student_id=9)
    db = _FakeSession(
        {
            ClassSession: session_obj,
            Class: _class_obj(),
            ClassEnrollment: enrollment,
        }
    )

    result = submit_or_update_attendance(
        session_id=55,
        data=AttendanceBulkData(
            records=[AttendanceRecordData(student_id=9, status="excused")]
        ),
        db=db,
        current_user=_user("admin"),
    )

    assert result["message"] == "Attendance saved successfully"
    assert db.committed is True


def test_mentor_can_mark_review_attendance():
    session_obj = SimpleNamespace(
        id=55,
        class_id=10,
        session_type="review",
        subject="verbal",
    )
    enrollment = SimpleNamespace(class_id=10, student_id=9)
    db = _FakeSession(
        {
            ClassSession: session_obj,
            Class: _class_obj(),
            ClassEnrollment: enrollment,
        }
    )

    result = submit_or_update_attendance(
        session_id=55,
        data=AttendanceBulkData(
            records=[AttendanceRecordData(student_id=9, status="present")]
        ),
        db=db,
        current_user=_user("mentor"),
    )

    assert result["session_id"] == 55


def test_teacher_cannot_mark_review_attendance():
    session_obj = SimpleNamespace(
        id=55,
        class_id=10,
        session_type="review",
        subject="math",
    )
    db = _FakeSession(
        {
            ClassSession: session_obj,
            Class: _class_obj(),
        }
    )

    with pytest.raises(HTTPException) as exc:
        submit_or_update_attendance(
            session_id=55,
            data=AttendanceBulkData(
                records=[AttendanceRecordData(student_id=9, status="absent")]
            ),
            db=db,
            current_user=_user("teacher", user_id=3),
        )

    assert exc.value.status_code == 403


def test_student_mark_review_attendance_returns_403(client: TestClient):
    app.dependency_overrides[methods_get_current_user] = lambda: _user("student")

    response = client.post(
        "/attendance/sessions/55",
        json={"records": [{"student_id": 9, "status": "present"}]},
    )

    assert response.status_code == 403


def test_teacher_can_still_create_math_session():
    db = _db_with_class()

    result = create_session(
        class_id=10,
        data=CreateSessionData(
            date=date(2026, 8, 10),
            session_type="math",
            start_time=time(8, 0),
            end_time=time(9, 30),
        ),
        db=db,
        current_user=_user("teacher", user_id=3),
    )

    assert result["session_type"] == "math"
    assert result["subject"] is None


def test_build_session_specs_include_review_subjects():
    from routers.classes_router import build_session_specs_for_range

    class_obj = _class_obj()
    verbal_review = [SimpleNamespace(day_of_week=6, start_time=time(10, 0), end_time=None)]
    math_review = [SimpleNamespace(day_of_week=6, start_time=time(12, 0), end_time=None)]

    specs = build_session_specs_for_range(
        class_obj,
        date(2026, 8, 9),
        date(2026, 8, 16),
        [],
        [],
        [],
        verbal_review_slots=verbal_review,
        math_review_slots=math_review,
    )

    assert (date(2026, 8, 9), "review", "verbal") in specs
    assert (date(2026, 8, 9), "review", "math") in specs
    assert (date(2026, 8, 16), "review", "verbal") in specs
    assert specs[(date(2026, 8, 9), "review", "verbal")]["subject"] == "verbal"
    assert specs[(date(2026, 8, 9), "review", "math")]["subject"] == "math"
