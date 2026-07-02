from __future__ import annotations

from datetime import datetime
from urllib.parse import unquote

import mimetypes

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from dependencies.auth import AuthUser, get_current_user, normalize_role
from dependencies.filters import homework_results_query
from Methods.auth import (
    get_db,
    get_current_user as legacy_get_current_user,
    require_roles as legacy_require_roles,
)
from models import (
    Assignment,
    HomeworkResult,
    User,
)
from schemas.homework_result import (
    HomeworkFileSchema,
    HomeworkResultSchema,
    HomeworkUploadResponse,
    ReturnHomeworkRequest,
)
from services.attachments import (
    all_archived_public_ids,
    append_submission_history,
    append_uploads,
    attachment_public_ids,
    find_history_entry,
    first_image_url,
    first_image_url_from_uploads,
    parse_uploaded_at,
    read_attachments,
    read_original_attachments,
    read_submission_history,
    remove_history_attachment,
    snapshot_original_attachments,
    write_attachments,
)
from services.cloudinary_service import (
    delete_file,
    rollback_uploads,
    upload_file,
)

router = APIRouter(tags=["homework-results"])

MAX_FILES = 10
MAX_FILE_SIZE_BYTES = 52_428_800
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/heic",
    "application/pdf",
}

EXTENSION_TO_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "heic": "image/heic",
    "pdf": "application/pdf",
}


def resolve_upload_content_type(filename: str, reported: str | None) -> str | None:
    normalized = (reported or "").split(";", 1)[0].strip().lower()
    if normalized in ALLOWED_CONTENT_TYPES:
        return normalized

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in EXTENSION_TO_MIME:
        return EXTENSION_TO_MIME[ext]

    guessed, _ = mimetypes.guess_type(filename)
    if guessed and guessed in ALLOWED_CONTENT_TYPES:
        return guessed

    return None


def validation_error(payload: dict) -> JSONResponse:
    return JSONResponse(status_code=422, content=payload)


def conflict_error(payload: dict) -> JSONResponse:
    return JSONResponse(status_code=409, content=payload)


def get_homework_result_or_404(result_id: int, db: Session) -> HomeworkResult:
    result = db.query(HomeworkResult).filter(HomeworkResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Homework result not found")
    return result


def get_assignment_for_result(result: HomeworkResult, db: Session) -> Assignment:
    assignment = db.query(Assignment).filter(Assignment.id == result.assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return assignment


def attachment_items_for_api(items: list[dict]) -> list[dict]:
    payload: list[dict] = []
    for item in items:
        row = dict(item)
        if "uploaded_at" in row:
            row["uploaded_at"] = parse_uploaded_at(row["uploaded_at"])
        payload.append(row)
    return payload


def serialize_homework_result(result: HomeworkResult) -> dict:
    attachments = read_attachments(result.attachments)
    original_attachments = read_original_attachments(result)
    payload = HomeworkResultSchema(
        id=result.id,
        assignment_id=result.assignment_id,
        submitted=result.submitted,
        submitted_at=result.submitted_at,
        photo_link=result.photo_link,
        correct_total=result.correct_total,
        incorrect_total=result.incorrect_total,
        analysis=result.analysis,
        returned_at=result.returned_at,
        returned_by_id=result.returned_by_id,
        return_reason=result.return_reason,
        attachments=[
            HomeworkFileSchema.model_validate(item)
            for item in attachment_items_for_api(attachments)
        ],
        original_attachments=[
            HomeworkFileSchema.model_validate(item)
            for item in attachment_items_for_api(original_attachments)
        ],
        legacy_photo=len(attachments) == 0 and result.photo_link is not None,
    )
    return payload.model_dump(mode="json")


def serialize_history_homework_result(result: HomeworkResult, history_id: int) -> dict:
    entry = find_history_entry(result, history_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Historical submission not found")

    attachments = read_attachments(entry.get("attachments"))
    submitted_at = entry.get("submitted_at")
    if submitted_at is not None and not isinstance(submitted_at, datetime):
        submitted_at = parse_uploaded_at(submitted_at)

    returned_at = entry.get("returned_at")
    if returned_at is not None and not isinstance(returned_at, datetime):
        returned_at = parse_uploaded_at(returned_at)

    payload = HomeworkResultSchema(
        id=result.id,
        assignment_id=result.assignment_id,
        submitted=True,
        submitted_at=submitted_at,
        photo_link=first_image_url(attachments) or result.photo_link,
        correct_total=entry.get("correct_total"),
        incorrect_total=entry.get("incorrect_total"),
        analysis=entry.get("analysis"),
        returned_at=returned_at,
        returned_by_id=result.returned_by_id,
        return_reason=entry.get("return_reason"),
        attachments=[
            HomeworkFileSchema.model_validate(item)
            for item in attachment_items_for_api(attachments)
        ],
        original_attachments=[
            HomeworkFileSchema.model_validate(item)
            for item in attachment_items_for_api(attachments)
        ],
        history_id=history_id,
        is_historical=True,
        legacy_photo=False,
    )
    return payload.model_dump(mode="json")


def serialize_upload_response(result: HomeworkResult) -> dict:
    attachments = read_attachments(result.attachments)
    payload = HomeworkUploadResponse(
        id=result.id,
        submitted=result.submitted,
        photo_link=result.photo_link,
        attachments=[
            HomeworkFileSchema.model_validate(item)
            for item in attachment_items_for_api(attachments)
        ],
    )
    return payload.model_dump(mode="json")


async def validate_upload_files(files: list[UploadFile]) -> list[dict] | JSONResponse:
    if not files:
        raise HTTPException(status_code=422, detail="At least one file is required")

    validated: list[dict] = []
    for upload in files:
        filename = upload.filename or "upload"
        content_type = resolve_upload_content_type(filename, upload.content_type)
        if content_type is None:
            return validation_error(
                {
                    "error": "INVALID_FILE_TYPE",
                    "filename": filename,
                    "content_type": upload.content_type or "",
                }
            )

        file_bytes = await upload.read()
        if len(file_bytes) > MAX_FILE_SIZE_BYTES:
            return validation_error(
                {
                    "error": "FILE_TOO_LARGE",
                    "filename": filename,
                    "max_mb": 50,
                }
            )

        validated.append(
            {
                "filename": filename,
                "content_type": content_type,
                "bytes": file_bytes,
            }
        )

    return validated


async def resolve_upload_files(request: Request) -> list[UploadFile]:
    form = await request.form()
    return [
        item
        for item in (form.getlist("files[]") or form.getlist("files"))
        if isinstance(item, UploadFile)
    ]


@router.get("/homework-results")
def list_homework_results(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    results = homework_results_query(db, current_user).order_by(HomeworkResult.id.asc()).all()
    return [serialize_homework_result(result) for result in results]


@router.get("/homework-results/{result_id}", response_model=HomeworkResultSchema)
def get_homework_result(
    result_id: int,
    history_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    result = (
        homework_results_query(db, current_user)
        .filter(HomeworkResult.id == result_id)
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Homework result not found")

    if history_id is not None:
        return serialize_history_homework_result(result, history_id)

    return serialize_homework_result(result)


@router.post("/homework-results/{result_id}/return")
def return_homework_for_revision(
    result_id: int,
    body: ReturnHomeworkRequest,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    role = normalize_role(current_user.role)
    if role not in ("admin", "mentor", "teacher"):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    result = (
        homework_results_query(db, current_user)
        .filter(HomeworkResult.id == result_id)
        .first()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Homework result not found")

    if not result.submitted:
        if result.returned_at is not None:
            return conflict_error(
                {
                    "error": "ALREADY_RETURNED",
                    "detail": "Homework is already pending revision",
                }
            )
        return conflict_error(
            {
                "error": "NOT_SUBMITTED",
                "detail": "Homework has not been submitted yet",
            }
        )

    reason = body.reason.strip() if body.reason and body.reason.strip() else None
    returned_at = datetime.utcnow()
    append_submission_history(
        result,
        submitted_at=result.submitted_at,
        correct_total=result.correct_total,
        incorrect_total=result.incorrect_total,
        analysis=result.analysis,
        attachments=read_attachments(result.attachments),
        returned_at=returned_at,
        return_reason=reason,
    )
    snapshot_original_attachments(result)
    result.submitted = False
    result.submitted_at = None
    result.returned_at = returned_at
    result.returned_by_id = current_user.id
    result.return_reason = reason
    result.correct_total = None
    result.incorrect_total = None
    result.analysis = None

    db.commit()
    db.refresh(result)

    return serialize_homework_result(result)


@router.post(
    "/homework-results/{result_id}/upload",
    response_model=HomeworkUploadResponse,
)
async def upload_homework_result_files(
    result_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(legacy_require_roles(["student"])),
):
    if not files:
        raise HTTPException(status_code=422, detail="At least one file is required")

    result = get_homework_result_or_404(result_id, db)
    assignment = get_assignment_for_result(result, db)

    if current_user.id != assignment.student_id:
        raise HTTPException(
            status_code=403,
            detail="Students can upload only for their own homework results",
        )

    existing_attachments = read_attachments(result.attachments)

    if len(existing_attachments) + len(files) > MAX_FILES:
        return validation_error(
            {
                "error": "TOO_MANY_FILES",
                "max": MAX_FILES,
                "current_count": len(existing_attachments),
            }
        )

    validation = await validate_upload_files(files)
    if isinstance(validation, JSONResponse):
        return validation

    uploaded_cloudinary: list[dict] = []
    try:
        for item in validation:
            uploaded_cloudinary.append(
                upload_file(
                    item["bytes"],
                    result_id=result_id,
                    filename=item["filename"],
                    content_type=item["content_type"],
                )
            )
    except HTTPException:
        rollback_uploads(uploaded_cloudinary)
        raise
    except Exception:
        rollback_uploads(uploaded_cloudinary)
        raise HTTPException(status_code=500, detail="Upload failed, rolled back")

    try:
        write_attachments(
            result,
            append_uploads(existing_attachments, uploaded_cloudinary),
        )
        if result.photo_link is None:
            first_image = first_image_url_from_uploads(uploaded_cloudinary)
            if first_image is not None:
                result.photo_link = first_image

        db.commit()
        db.refresh(result)
    except Exception:
        db.rollback()
        rollback_uploads(uploaded_cloudinary)
        raise HTTPException(status_code=500, detail="Upload failed, rolled back")

    return serialize_upload_response(result)


@router.delete(
    "/homework-results/{result_id}/attachments/{public_id:path}",
    status_code=204,
)
def delete_homework_attachment(
    result_id: int,
    public_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(legacy_get_current_user),
):
    decoded_public_id = unquote(public_id)
    result = get_homework_result_or_404(result_id, db)
    assignment = get_assignment_for_result(result, db)

    role = normalize_role(current_user.role)
    if role == "admin":
        pass
    elif role == "student":
        if current_user.id != assignment.student_id:
            raise HTTPException(status_code=403, detail="Not enough permissions")
        if result.submitted and result.returned_at is None:
            raise HTTPException(
                status_code=403,
                detail="Cannot delete files from a submitted homework result",
            )
    else:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    attachments = read_attachments(result.attachments)

    target = next(
        (item for item in attachments if item.get("public_id") == decoded_public_id),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    archived_ids = all_archived_public_ids(result)
    in_archive = decoded_public_id in archived_ids
    is_admin_active_submission = (
        role == "admin" and result.submitted and result.returned_at is None
    )

    if role == "admin" and in_archive and not is_admin_active_submission:
        raise HTTPException(
            status_code=403,
            detail="Delete archived submission files from the submission history view",
        )

    updated_attachments = [
        item for item in attachments if item.get("public_id") != decoded_public_id
    ]
    write_attachments(result, updated_attachments)

    if is_admin_active_submission or not in_archive:
        delete_file(decoded_public_id, target.get("content_type", "image/jpeg"))

    deleted_url = target.get("url")
    if result.photo_link == deleted_url:
        result.photo_link = first_image_url(updated_attachments)

    db.commit()
    return Response(status_code=204)


@router.delete(
    "/homework-results/{result_id}/history/{history_id}/attachments/{public_id:path}",
    status_code=204,
)
def delete_history_homework_attachment(
    result_id: int,
    history_id: int,
    public_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(legacy_get_current_user),
):
    if normalize_role(current_user.role) != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")

    decoded_public_id = unquote(public_id)
    result = get_homework_result_or_404(result_id, db)

    removed = remove_history_attachment(result, history_id, decoded_public_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    delete_file(decoded_public_id, removed.get("content_type", "image/jpeg"))
    db.commit()
    return Response(status_code=204)
