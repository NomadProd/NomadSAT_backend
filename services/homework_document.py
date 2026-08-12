from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse

from models import Assignment

PDF_MAGIC = b"%PDF-"
MAX_HOMEWORK_PDF_MB = 50
MAX_HOMEWORK_PDF_BYTES = MAX_HOMEWORK_PDF_MB * 1024 * 1024
HOMEWORK_DOCUMENT_FOLDER = "homework_documents"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def validation_error(payload: dict) -> JSONResponse:
    return JSONResponse(status_code=422, content=payload)


def validate_homework_pdf(
    *,
    filename: str,
    content_type: str | None,
    file_bytes: bytes,
) -> JSONResponse | None:
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type != "application/pdf":
        return validation_error(
            {
                "error": "INVALID_FILE_TYPE",
                "filename": filename,
                "detail": "Only PDF files are allowed",
            }
        )

    if len(file_bytes) > MAX_HOMEWORK_PDF_BYTES:
        return validation_error(
            {
                "error": "FILE_TOO_LARGE",
                "filename": filename,
                "max_mb": MAX_HOMEWORK_PDF_MB,
                "detail": "The PDF must not exceed 50 MB",
            }
        )

    if not file_bytes.startswith(PDF_MAGIC):
        return validation_error(
            {
                "error": "INVALID_PDF",
                "filename": filename,
                "detail": "The uploaded file is not a valid PDF",
            }
        )

    return None


def read_homework_document(raw: Any) -> dict | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return dict(raw)


def homework_document_for_api(
    raw: Any,
    *,
    include_public_id: bool = False,
    include_secure_url: bool = False,
    default_filename: str = "homework.pdf",
) -> dict | None:
    document = read_homework_document(raw)
    if document is None:
        return None

    url = document.get("secure_url") or document.get("url")
    if not url:
        return None

    payload: dict[str, Any] = {
        "url": url,
        "filename": document.get("filename") or default_filename,
        "content_type": document.get("content_type") or "application/pdf",
        "size_bytes": int(document.get("size_bytes") or 0),
        "uploaded_at": document.get("uploaded_at"),
    }
    if include_secure_url:
        payload["secure_url"] = document.get("secure_url") or url
    if include_public_id and document.get("public_id"):
        payload["public_id"] = document["public_id"]
    return payload


def homework_document_upload_payload(raw: Any) -> dict:
    payload = homework_document_for_api(
        raw,
        include_public_id=True,
        include_secure_url=True,
    )
    if payload is None:
        raise ValueError("homework_document metadata is missing")
    return payload


def mock_document_for_api(
    raw: Any,
    *,
    include_public_id: bool = False,
    include_secure_url: bool = False,
) -> dict | None:
    return homework_document_for_api(
        raw,
        include_public_id=include_public_id,
        include_secure_url=include_secure_url,
        default_filename="mock_test.pdf",
    )


def mock_document_upload_payload(raw: Any) -> dict:
    payload = mock_document_for_api(
        raw,
        include_public_id=True,
        include_secure_url=True,
    )
    if payload is None:
        raise ValueError("mock_document metadata is missing")
    return payload


def serialize_assignment(assignment: Assignment) -> dict:
    return {
        "assignment_id": assignment.id,
        "session_id": assignment.session_id,
        "student_id": assignment.student_id,
        "slot_index": assignment.slot_index,
        "title": assignment.title,
        "instruction": assignment.instruction,
        "task_link": assignment.task_link,
        "due_date": assignment.due_date,
        "due_time": assignment.due_time,
        "photo_required": assignment.photo_required,
        "homework_document": homework_document_for_api(assignment.homework_document),
    }
