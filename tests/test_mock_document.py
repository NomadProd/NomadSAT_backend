from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from dependencies.auth import AuthUser, get_current_user
from main import app
from Methods.auth import get_db
from models import Session as ClassSession
from services import homework_document as homework_document_service

TINY_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
OLD_PUBLIC_ID = "mock_documents/session_456_oldfile"
NEW_PUBLIC_ID = "mock_documents/session_456_newfile"


class FakeClassSession:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 456)
        self.class_id = kwargs.get("class_id", 10)
        self.teacher_id = kwargs.get("teacher_id", 3)
        self.date = kwargs.get("date", None)
        self.start_time = kwargs.get("start_time", None)
        self.end_time = kwargs.get("end_time", None)
        self.session_type = kwargs.get("session_type", "mock")
        self.subject = kwargs.get("subject", None)
        self.topic = kwargs.get("topic", "SAT Mock")
        self.academic_plan_item_id = kwargs.get("academic_plan_item_id", None)
        self.lesson_notes = kwargs.get("lesson_notes", None)
        self.mock_document = kwargs.get("mock_document", None)


class FakeQuery:
    def __init__(self, session: "FakeDb", model):
        self.session = session
        self.model = model

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        if self.model is ClassSession:
            if not self.session.session_visible:
                return None
            return self.session.session_obj
        return None

    def all(self):
        if self.model is ClassSession and self.session.session_obj is not None:
            if not self.session.session_visible:
                return []
            return [self.session.session_obj]
        return []


class FakeDb:
    def __init__(
        self,
        session_obj: FakeClassSession | None = None,
        *,
        commit_error: Exception | None = None,
        session_visible: bool = True,
    ):
        self.session_obj = session_obj
        self.commit_error = commit_error
        self.session_visible = session_visible
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


def _override_db(db: FakeDb) -> None:
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db


def _old_document() -> dict:
    return {
        "url": "https://res.cloudinary.com/demo/raw/upload/old_mock.pdf",
        "secure_url": "https://res.cloudinary.com/demo/raw/upload/old_mock.pdf",
        "public_id": OLD_PUBLIC_ID,
        "filename": "old_mock.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1000,
        "uploaded_at": "2026-01-01T00:00:00Z",
        "uploaded_by_id": 1,
    }


def _stub_cloudinary(monkeypatch, *, upload_error: Exception | None = None):
    upload_calls: list[dict] = []
    delete_calls: list[str] = []

    def fake_upload(file_bytes, *, session_id: int, filename: str):
        if upload_error is not None:
            raise upload_error
        upload_calls.append(
            {
                "session_id": session_id,
                "filename": filename,
                "size_bytes": len(file_bytes),
            }
        )
        return {
            "url": "https://res.cloudinary.com/demo/raw/upload/new_mock.pdf",
            "secure_url": "https://res.cloudinary.com/demo/raw/upload/new_mock.pdf",
            "public_id": NEW_PUBLIC_ID,
            "filename": filename,
            "content_type": "application/pdf",
            "size_bytes": len(file_bytes),
        }

    def fake_delete(public_id: str):
        delete_calls.append(public_id)

    monkeypatch.setattr(
        "routers.sessions_router.upload_mock_document",
        fake_upload,
    )
    monkeypatch.setattr("routers.sessions_router.delete_raw_file", fake_delete)
    monkeypatch.setattr("routers.sessions_router.flag_modified", lambda *_a, **_k: None)
    return upload_calls, delete_calls


def _upload(
    client: TestClient,
    *,
    filename: str = "sat_mock_3_full.pdf",
    content: bytes = TINY_PDF,
    content_type: str = "application/pdf",
    session_id: int = 456,
):
    return client.post(
        f"/sessions/{session_id}/mock-document",
        files={"file": (filename, BytesIO(content), content_type)},
    )


def test_admin_can_upload_mock_pdf(client: TestClient, monkeypatch):
    session_obj = FakeClassSession()
    _override_user("admin")
    _override_db(FakeDb(session_obj))
    upload_calls, delete_calls = _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == 456
    assert body["mock_document"]["filename"] == "sat_mock_3_full.pdf"
    assert body["mock_document"]["content_type"] == "application/pdf"
    assert body["mock_document"]["public_id"] == NEW_PUBLIC_ID
    assert session_obj.mock_document["uploaded_by_id"] == 1
    assert len(upload_calls) == 1
    assert delete_calls == []


def test_mentor_can_upload_mock_pdf(client: TestClient, monkeypatch):
    session_obj = FakeClassSession()
    _override_user("mentor", user_id=2)
    _override_db(FakeDb(session_obj))
    _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 200
    assert session_obj.mock_document is not None
    assert session_obj.mock_document["uploaded_by_id"] == 2


def test_teacher_upload_returns_403(client: TestClient, monkeypatch):
    session_obj = FakeClassSession()
    _override_user("teacher", user_id=3)
    _override_db(FakeDb(session_obj))
    upload_calls, _ = _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 403
    assert session_obj.mock_document is None
    assert upload_calls == []


def test_student_upload_returns_403(client: TestClient, monkeypatch):
    session_obj = FakeClassSession()
    _override_user("student", user_id=20)
    _override_db(FakeDb(session_obj))
    upload_calls, _ = _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 403
    assert session_obj.mock_document is None
    assert upload_calls == []


def test_non_pdf_file_returns_422(client: TestClient, monkeypatch):
    session_obj = FakeClassSession()
    _override_user("admin")
    _override_db(FakeDb(session_obj))
    upload_calls, _ = _stub_cloudinary(monkeypatch)

    response = _upload(
        client,
        filename="notes.docx",
        content=b"PK\x03\x04not-a-pdf",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": "INVALID_FILE_TYPE",
        "filename": "notes.docx",
        "detail": "Only PDF files are allowed",
    }
    assert upload_calls == []


def test_renamed_non_pdf_with_pdf_extension_returns_422(client: TestClient, monkeypatch):
    session_obj = FakeClassSession()
    _override_user("admin")
    _override_db(FakeDb(session_obj))
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
    session_obj = FakeClassSession()
    _override_user("admin")
    _override_db(FakeDb(session_obj))
    upload_calls, _ = _stub_cloudinary(monkeypatch)
    monkeypatch.setattr(homework_document_service, "MAX_HOMEWORK_PDF_BYTES", 64)

    oversized = b"%PDF-" + b"0" * 60
    response = _upload(client, filename="mock_test.pdf", content=oversized)

    assert response.status_code == 422
    assert response.json() == {
        "error": "FILE_TOO_LARGE",
        "filename": "mock_test.pdf",
        "max_mb": 50,
        "detail": "The PDF must not exceed 50 MB",
    }
    assert upload_calls == []


def test_upload_to_non_mock_session_is_rejected(client: TestClient, monkeypatch):
    session_obj = FakeClassSession(session_type="verbal")
    _override_user("admin")
    _override_db(FakeDb(session_obj))
    upload_calls, _ = _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_SESSION_TYPE"
    assert upload_calls == []
    assert session_obj.mock_document is None


def test_replace_uploads_new_file_before_deleting_old(client: TestClient, monkeypatch):
    session_obj = FakeClassSession(mock_document=_old_document())
    _override_user("mentor")
    _override_db(FakeDb(session_obj))
    call_order: list[str] = []

    def fake_upload(file_bytes, *, session_id: int, filename: str):
        call_order.append("upload")
        return {
            "url": "https://res.cloudinary.com/demo/raw/upload/new_mock.pdf",
            "secure_url": "https://res.cloudinary.com/demo/raw/upload/new_mock.pdf",
            "public_id": NEW_PUBLIC_ID,
            "filename": filename,
            "content_type": "application/pdf",
            "size_bytes": len(file_bytes),
        }

    def fake_delete(public_id: str):
        call_order.append(f"delete:{public_id}")

    monkeypatch.setattr("routers.sessions_router.upload_mock_document", fake_upload)
    monkeypatch.setattr("routers.sessions_router.delete_raw_file", fake_delete)
    monkeypatch.setattr("routers.sessions_router.flag_modified", lambda *_a, **_k: None)

    response = _upload(client, filename="new_mock.pdf")

    assert response.status_code == 200
    assert call_order == ["upload", f"delete:{OLD_PUBLIC_ID}"]
    assert session_obj.mock_document["public_id"] == NEW_PUBLIC_ID


def test_admin_can_delete_mock_document(client: TestClient, monkeypatch):
    session_obj = FakeClassSession(mock_document=_old_document())
    _override_user("admin")
    _override_db(FakeDb(session_obj))
    _, delete_calls = _stub_cloudinary(monkeypatch)

    response = client.delete("/sessions/456/mock-document")

    assert response.status_code == 204
    assert session_obj.mock_document is None
    assert delete_calls == [OLD_PUBLIC_ID]


def test_mentor_can_delete_mock_document(client: TestClient, monkeypatch):
    session_obj = FakeClassSession(mock_document=_old_document())
    _override_user("mentor")
    _override_db(FakeDb(session_obj))
    _, delete_calls = _stub_cloudinary(monkeypatch)

    response = client.delete("/sessions/456/mock-document")

    assert response.status_code == 204
    assert session_obj.mock_document is None
    assert delete_calls == [OLD_PUBLIC_ID]


def test_teacher_delete_returns_403(client: TestClient, monkeypatch):
    session_obj = FakeClassSession(mock_document=_old_document())
    _override_user("teacher")
    _override_db(FakeDb(session_obj))
    _, delete_calls = _stub_cloudinary(monkeypatch)

    response = client.delete("/sessions/456/mock-document")

    assert response.status_code == 403
    assert session_obj.mock_document is not None
    assert delete_calls == []


def test_teacher_can_get_mock_document_for_own_session(client: TestClient):
    session_obj = FakeClassSession(
        teacher_id=3,
        mock_document=_old_document(),
    )
    _override_user("teacher", user_id=3)
    _override_db(FakeDb(session_obj, session_visible=True))

    response = client.get("/sessions/456")

    assert response.status_code == 200
    document = response.json()["mock_document"]
    assert document["filename"] == "old_mock.pdf"
    assert "public_id" not in document
    assert "uploaded_by_id" not in document


def test_student_can_get_mock_document_for_enrolled_class(client: TestClient):
    session_obj = FakeClassSession(mock_document=_old_document())
    _override_user("student", user_id=20)
    _override_db(FakeDb(session_obj, session_visible=True))

    response = client.get("/sessions/456")

    assert response.status_code == 200
    document = response.json()["mock_document"]
    assert document["url"].startswith("https://")
    assert "public_id" not in document


def test_student_from_other_class_cannot_get_mock_document(client: TestClient):
    session_obj = FakeClassSession(mock_document=_old_document())
    _override_user("student", user_id=99)
    _override_db(FakeDb(session_obj, session_visible=False))

    response = client.get("/sessions/456")

    assert response.status_code == 404


def test_teacher_cannot_get_mock_document_for_other_class(client: TestClient):
    session_obj = FakeClassSession(
        teacher_id=8,
        mock_document=_old_document(),
    )
    _override_user("teacher", user_id=3)
    _override_db(FakeDb(session_obj, session_visible=False))

    response = client.get("/sessions/456")

    assert response.status_code == 404


def test_get_returns_null_when_no_mock_document(client: TestClient):
    session_obj = FakeClassSession(mock_document=None)
    _override_user("student", user_id=20)
    _override_db(FakeDb(session_obj, session_visible=True))

    response = client.get("/sessions/456")

    assert response.status_code == 200
    assert response.json()["mock_document"] is None


def test_replace_keeps_old_pdf_when_upload_fails(client: TestClient, monkeypatch):
    from fastapi import HTTPException

    old_document = _old_document()
    session_obj = FakeClassSession(mock_document=dict(old_document))
    _override_user("admin")
    _override_db(FakeDb(session_obj))
    _stub_cloudinary(
        monkeypatch,
        upload_error=HTTPException(
            status_code=500,
            detail="Upload failed, rolled back",
        ),
    )

    response = _upload(client)

    assert response.status_code == 500
    assert session_obj.mock_document == old_document


def test_replace_deletes_new_file_when_database_update_fails(
    client: TestClient, monkeypatch
):
    old_document = _old_document()
    session_obj = FakeClassSession(mock_document=dict(old_document))
    db = FakeDb(session_obj, commit_error=RuntimeError("db unavailable"))
    _override_user("admin")
    _override_db(db)
    upload_calls, delete_calls = _stub_cloudinary(monkeypatch)

    response = _upload(client)

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to save mock test document"
    assert session_obj.mock_document == old_document
    assert len(upload_calls) == 1
    assert delete_calls == [NEW_PUBLIC_ID]
    assert db.rolled_back is True


def test_delete_missing_pdf_returns_404(client: TestClient, monkeypatch):
    session_obj = FakeClassSession(mock_document=None)
    _override_user("admin")
    _override_db(FakeDb(session_obj))
    _, delete_calls = _stub_cloudinary(monkeypatch)

    response = client.delete("/sessions/456/mock-document")

    assert response.status_code == 404
    assert delete_calls == []
