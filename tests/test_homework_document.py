from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from dependencies.auth import AuthUser, get_current_user
from main import app
from Methods.auth import get_db
from models import Assignment
from services import homework_document as homework_document_service

TINY_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
OLD_TASK_LINK = "https://example.com/legacy-task"
OLD_PUBLIC_ID = "homework_documents/assignment_1_oldfile"
NEW_PUBLIC_ID = "homework_documents/assignment_1_newfile"


class FakeAssignment:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 1)
        self.session_id = kwargs.get("session_id", 10)
        self.student_id = kwargs.get("student_id", 20)
        self.slot_index = kwargs.get("slot_index", 1)
        self.title = kwargs.get("title", "Homework 1")
        self.instruction = kwargs.get("instruction", "Solve the problems")
        self.task_link = kwargs.get("task_link", OLD_TASK_LINK)
        self.due_date = kwargs.get("due_date", None)
        self.due_time = kwargs.get("due_time", None)
        self.photo_required = kwargs.get("photo_required", False)
        self.homework_document = kwargs.get("homework_document", None)


class FakeQuery:
    def __init__(self, session: "FakeSession", model):
        self.session = session
        self.model = model

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        if self.model is Assignment:
            return self.session.assignment
        return None

    def all(self):
        if self.model is Assignment and self.session.assignment is not None:
            return [self.session.assignment]
        return []


class FakeSession:
    def __init__(self, assignment: FakeAssignment | None = None, commit_error: Exception | None = None):
        self.assignment = assignment
        self.commit_error = commit_error
        self.committed = False
        self.rolled_back = False

    def query(self, model):
        return FakeQuery(self, model)

    def commit(self):
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True

    def refresh(self, _obj):
        return None

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _override_user(role: str, user_id: int = 1) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthUser(id=user_id, role=role)


def _override_db(session: FakeSession) -> None:
    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db


def _old_document() -> dict:
    return {
        "url": "https://res.cloudinary.com/demo/raw/upload/old.pdf",
        "secure_url": "https://res.cloudinary.com/demo/raw/upload/old.pdf",
        "public_id": OLD_PUBLIC_ID,
        "filename": "old_homework.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1000,
        "uploaded_at": "2026-01-01T00:00:00Z",
    }


def _stub_cloudinary(monkeypatch, *, upload_error: Exception | None = None):
    upload_calls: list[dict] = []
    delete_calls: list[str] = []

    def fake_upload(file_bytes, *, assignment_id: int, filename: str):
        if upload_error is not None:
            raise upload_error
        upload_calls.append(
            {
                "assignment_id": assignment_id,
                "filename": filename,
                "size_bytes": len(file_bytes),
            }
        )
        return {
            "url": "https://res.cloudinary.com/demo/raw/upload/new.pdf",
            "secure_url": "https://res.cloudinary.com/demo/raw/upload/new.pdf",
            "public_id": NEW_PUBLIC_ID,
            "filename": filename,
            "content_type": "application/pdf",
            "size_bytes": len(file_bytes),
        }

    def fake_delete(public_id: str):
        delete_calls.append(public_id)

    monkeypatch.setattr(
        "routers.assignments_router.upload_homework_document",
        fake_upload,
    )
    monkeypatch.setattr("routers.assignments_router.delete_raw_file", fake_delete)
    monkeypatch.setattr("routers.assignments_router.flag_modified", lambda *_args, **_kwargs: None)
    return upload_calls, delete_calls


def _upload(
    client: TestClient,
    *,
    filename: str = "algebra_homework.pdf",
    content: bytes = TINY_PDF,
    content_type: str = "application/pdf",
    assignment_id: int = 1,
):
    return client.post(
        f"/assignments/{assignment_id}/homework-document",
        files={"file": (filename, BytesIO(content), content_type)},
    )


def test_max_pdf_limit_is_50mb():
    assert homework_document_service.MAX_HOMEWORK_PDF_MB == 50
    assert homework_document_service.MAX_HOMEWORK_PDF_BYTES == 50 * 1024 * 1024


def test_admin_can_upload_valid_pdf(client: TestClient, monkeypatch):
    assignment = FakeAssignment()
    session = FakeSession(assignment)
    _override_user("admin")
    _override_db(session)
    upload_calls, delete_calls = _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 200
    body = response.json()
    assert body["assignment_id"] == 1
    assert body["homework_document"]["filename"] == "algebra_homework.pdf"
    assert body["homework_document"]["content_type"] == "application/pdf"
    assert body["homework_document"]["secure_url"].startswith("https://")
    assert body["homework_document"]["public_id"] == NEW_PUBLIC_ID
    assert assignment.homework_document["public_id"] == NEW_PUBLIC_ID
    assert assignment.task_link == OLD_TASK_LINK
    assert len(upload_calls) == 1
    assert delete_calls == []


def test_mentor_can_upload_valid_pdf(client: TestClient, monkeypatch):
    assignment = FakeAssignment()
    _override_user("mentor", user_id=2)
    _override_db(FakeSession(assignment))
    _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 200
    assert assignment.homework_document is not None


def test_teacher_upload_returns_403(client: TestClient, monkeypatch):
    assignment = FakeAssignment()
    _override_user("teacher", user_id=3)
    _override_db(FakeSession(assignment))
    upload_calls, _ = _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 403
    assert assignment.homework_document is None
    assert upload_calls == []


def test_student_upload_returns_403(client: TestClient, monkeypatch):
    assignment = FakeAssignment()
    _override_user("student", user_id=20)
    _override_db(FakeSession(assignment))
    upload_calls, _ = _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 403
    assert assignment.homework_document is None
    assert upload_calls == []


def test_non_pdf_file_returns_422(client: TestClient, monkeypatch):
    assignment = FakeAssignment()
    _override_user("admin")
    _override_db(FakeSession(assignment))
    upload_calls, _ = _stub_cloudinary(monkeypatch)

    response = _upload(
        client,
        filename="document.docx",
        content=b"PK\x03\x04not-a-pdf",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": "INVALID_FILE_TYPE",
        "filename": "document.docx",
        "detail": "Only PDF files are allowed",
    }
    assert upload_calls == []
    assert assignment.homework_document is None


def test_renamed_non_pdf_with_pdf_extension_returns_422(client: TestClient, monkeypatch):
    assignment = FakeAssignment()
    _override_user("admin")
    _override_db(FakeSession(assignment))
    upload_calls, _ = _stub_cloudinary(monkeypatch)

    response = _upload(
        client,
        filename="fake.pdf",
        content=b"PK\x03\x04this-is-a-zip",
        content_type="application/pdf",
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": "INVALID_PDF",
        "filename": "fake.pdf",
        "detail": "The uploaded file is not a valid PDF",
    }
    assert upload_calls == []


def test_file_larger_than_50mb_returns_422(client: TestClient, monkeypatch):
    assignment = FakeAssignment()
    _override_user("admin")
    _override_db(FakeSession(assignment))
    upload_calls, _ = _stub_cloudinary(monkeypatch)
    monkeypatch.setattr(homework_document_service, "MAX_HOMEWORK_PDF_BYTES", 64)

    oversized = b"%PDF-" + b"0" * 60
    response = _upload(client, filename="large_homework.pdf", content=oversized)

    assert response.status_code == 422
    assert response.json() == {
        "error": "FILE_TOO_LARGE",
        "filename": "large_homework.pdf",
        "max_mb": 50,
        "detail": "The PDF must not exceed 50 MB",
    }
    assert upload_calls == []


def test_task_link_preserved_after_pdf_upload(client: TestClient, monkeypatch):
    assignment = FakeAssignment(task_link=OLD_TASK_LINK)
    _override_user("admin")
    _override_db(FakeSession(assignment))
    _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 200
    assert assignment.task_link == OLD_TASK_LINK


def test_replace_pdf_uploads_new_file_before_deleting_old(client: TestClient, monkeypatch):
    assignment = FakeAssignment(homework_document=_old_document())
    _override_user("mentor")
    _override_db(FakeSession(assignment))
    call_order: list[str] = []

    def fake_upload(file_bytes, *, assignment_id: int, filename: str):
        call_order.append("upload")
        return {
            "url": "https://res.cloudinary.com/demo/raw/upload/new.pdf",
            "secure_url": "https://res.cloudinary.com/demo/raw/upload/new.pdf",
            "public_id": NEW_PUBLIC_ID,
            "filename": filename,
            "content_type": "application/pdf",
            "size_bytes": len(file_bytes),
        }

    def fake_delete(public_id: str):
        call_order.append(f"delete:{public_id}")

    monkeypatch.setattr(
        "routers.assignments_router.upload_homework_document",
        fake_upload,
    )
    monkeypatch.setattr("routers.assignments_router.delete_raw_file", fake_delete)
    monkeypatch.setattr("routers.assignments_router.flag_modified", lambda *_a, **_k: None)

    response = _upload(client, filename="new_homework.pdf")

    assert response.status_code == 200
    assert call_order == ["upload", f"delete:{OLD_PUBLIC_ID}"]
    assert assignment.homework_document["public_id"] == NEW_PUBLIC_ID
    assert assignment.task_link == OLD_TASK_LINK


def test_replace_keeps_old_pdf_when_upload_fails(client: TestClient, monkeypatch):
    old_document = _old_document()
    assignment = FakeAssignment(homework_document=dict(old_document))
    _override_user("admin")
    _override_db(FakeSession(assignment))
    from fastapi import HTTPException

    _stub_cloudinary(
        monkeypatch,
        upload_error=HTTPException(status_code=500, detail="Upload failed, rolled back"),
    )

    response = _upload(client)

    assert response.status_code == 500
    assert assignment.homework_document == old_document


def test_replace_deletes_new_file_when_database_update_fails(client: TestClient, monkeypatch):
    old_document = _old_document()
    assignment = FakeAssignment(homework_document=dict(old_document))
    session = FakeSession(assignment, commit_error=RuntimeError("db unavailable"))
    _override_user("admin")
    _override_db(session)
    upload_calls, delete_calls = _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to save homework document"
    assert assignment.homework_document == old_document
    assert len(upload_calls) == 1
    assert delete_calls == [NEW_PUBLIC_ID]
    assert session.rolled_back is True


def test_delete_pdf_removes_cloudinary_file_and_clears_metadata(client: TestClient, monkeypatch):
    assignment = FakeAssignment(homework_document=_old_document(), task_link=OLD_TASK_LINK)
    _override_user("admin")
    _override_db(FakeSession(assignment))
    _, delete_calls = _stub_cloudinary(monkeypatch)

    response = client.delete("/assignments/1/homework-document")

    assert response.status_code == 204
    assert assignment.homework_document is None
    assert delete_calls == [OLD_PUBLIC_ID]


def test_delete_pdf_does_not_modify_task_link(client: TestClient, monkeypatch):
    assignment = FakeAssignment(homework_document=_old_document(), task_link=OLD_TASK_LINK)
    _override_user("mentor")
    _override_db(FakeSession(assignment))
    _stub_cloudinary(monkeypatch)

    response = client.delete("/assignments/1/homework-document")

    assert response.status_code == 204
    assert assignment.task_link == OLD_TASK_LINK


def test_teacher_delete_returns_403(client: TestClient, monkeypatch):
    assignment = FakeAssignment(homework_document=_old_document())
    _override_user("teacher")
    _override_db(FakeSession(assignment))
    _, delete_calls = _stub_cloudinary(monkeypatch)

    response = client.delete("/assignments/1/homework-document")

    assert response.status_code == 403
    assert assignment.homework_document is not None
    assert delete_calls == []


def test_student_delete_returns_403(client: TestClient, monkeypatch):
    assignment = FakeAssignment(homework_document=_old_document())
    _override_user("student", user_id=20)
    _override_db(FakeSession(assignment))
    _, delete_calls = _stub_cloudinary(monkeypatch)

    response = client.delete("/assignments/1/homework-document")

    assert response.status_code == 403
    assert assignment.homework_document is not None
    assert delete_calls == []


def test_delete_missing_pdf_returns_404(client: TestClient, monkeypatch):
    assignment = FakeAssignment(homework_document=None)
    _override_user("admin")
    _override_db(FakeSession(assignment))
    _stub_cloudinary(monkeypatch)

    response = client.delete("/assignments/1/homework-document")

    assert response.status_code == 404


def test_old_assignment_with_only_task_link_still_opens(client: TestClient):
    assignment = FakeAssignment(task_link=OLD_TASK_LINK, homework_document=None)
    _override_user("student", user_id=20)
    _override_db(FakeSession(assignment))

    response = client.get("/assignments/1")

    assert response.status_code == 200
    body = response.json()
    assert body["task_link"] == OLD_TASK_LINK
    assert body["homework_document"] is None
    assert "public_id" not in body


def test_get_assignment_hides_public_id_from_students(client: TestClient):
    assignment = FakeAssignment(homework_document=_old_document())
    _override_user("student", user_id=20)
    _override_db(FakeSession(assignment))

    response = client.get("/assignments/1")

    assert response.status_code == 200
    document = response.json()["homework_document"]
    assert document["filename"] == "old_homework.pdf"
    assert document["url"].startswith("https://")
    assert "public_id" not in document
