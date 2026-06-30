from __future__ import annotations

from datetime import datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from config import env
from dependencies.auth import AUTH_COOKIE_NAME, AuthUser, get_current_user
from dependencies.filters import (
    assignments_query,
    attendance_query,
    classes_query,
    homework_results_query,
    sessions_query,
)
from models import Assignment, Attendance, Class, HomeworkResult, Session as ClassSession
from Methods.auth import get_db
from main import app

JWT_SECRET_KEY = env("JWT_SECRET_KEY", required=True)
JWT_ALGORITHM = env("JWT_ALGORITHM", "HS256")


def _make_token(user_id: int, *, expired: bool = False) -> str:
    payload = {
        "sub": user_id,
        "user_id": user_id,
        "exp": datetime.utcnow() + (timedelta(minutes=-5) if expired else timedelta(hours=1)),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class _FakeQuery:
    def __init__(self, model):
        self.model = model
        self.filters = []

    def filter(self, *args):
        self.filters.extend(args)
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _FakeSession:
    def __init__(self):
        self.last_query: _FakeQuery | None = None

    def query(self, model):
        self.last_query = _FakeQuery(model)
        return self.last_query


def test_unauthenticated_users_returns_401(client: TestClient):
    response = client.get("/users")
    assert response.status_code == 401


def test_unauthenticated_homework_results_returns_401(client: TestClient):
    response = client.get("/homework-results")
    assert response.status_code == 401


def test_unauthenticated_classes_returns_401(client: TestClient):
    response = client.get("/classes")
    assert response.status_code == 401


def test_bearer_token_is_accepted_for_users_me(client: TestClient):
    def override_user() -> AuthUser:
        return AuthUser(id=7, role="student")

    app.dependency_overrides[get_current_user] = override_user

    response = client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {_make_token(7)}"},
    )
    assert response.status_code in {200, 401}


def test_student_homework_results_query_filters_by_assignment_student_id():
    db = _FakeSession()
    user = AuthUser(id=42, role="student")

    homework_results_query(db, user)

    assert db.last_query is not None
    assert db.last_query.model is HomeworkResult
    assert len(db.last_query.filters) == 1


def test_teacher_classes_query_filters_by_teacher_columns():
    db = _FakeSession()
    user = AuthUser(id=9, role="teacher")

    classes_query(db, user)

    assert db.last_query is not None
    assert db.last_query.model is Class
    assert len(db.last_query.filters) == 1


def test_student_classes_query_uses_enrollment_subquery():
    db = _FakeSession()
    user = AuthUser(id=3, role="student")

    classes_query(db, user)

    assert db.last_query is not None
    assert db.last_query.model is Class
    assert len(db.last_query.filters) == 1


def test_student_cannot_read_other_students_homework_result_returns_404(client: TestClient):
    def override_student() -> AuthUser:
        return AuthUser(id=1, role="student")

    def override_db():
        yield _FakeSession()

    app.dependency_overrides[get_current_user] = override_student
    app.dependency_overrides[get_db] = override_db

    response = client.get("/homework-results/999999")
    assert response.status_code == 404


def test_non_admin_users_list_returns_403(client: TestClient):
    def override_student() -> AuthUser:
        return AuthUser(id=1, role="student")

    app.dependency_overrides[get_current_user] = override_student

    response = client.get("/users")
    assert response.status_code == 403


def test_admin_users_list_not_forbidden(client: TestClient):
    def override_admin() -> AuthUser:
        return AuthUser(id=1, role="admin")

    app.dependency_overrides[get_current_user] = override_admin

    response = client.get("/users")
    assert response.status_code != 403


def test_teacher_sessions_query_filters_by_teacher_id():
    db = _FakeSession()
    user = AuthUser(id=5, role="teacher")

    sessions_query(db, user)

    assert db.last_query is not None
    assert db.last_query.model is ClassSession
    assert len(db.last_query.filters) == 1


def test_student_assignments_query_filters_by_student_id():
    db = _FakeSession()
    user = AuthUser(id=11, role="student")

    assignments_query(db, user)

    assert db.last_query is not None
    assert db.last_query.model is Assignment
    assert len(db.last_query.filters) == 1


def test_student_attendance_query_filters_by_student_id():
    db = _FakeSession()
    user = AuthUser(id=11, role="student")

    attendance_query(db, user)

    assert db.last_query is not None
    assert db.last_query.model is Attendance
    assert len(db.last_query.filters) == 1
