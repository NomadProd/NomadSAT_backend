from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from Methods.auth import get_db, require_roles
from models import MockResult, User
from routes.homework_results import validate_upload_files
from schemas.mock_result import MockFileSchema, MockResultSchema
from services.attachments import (
    append_uploads,
    attachment_dicts_to_api,
    first_image_url_from_uploads,
    read_attachments,
    write_attachments,
)
from services.cloudinary_service import delete_file, upload_file

router = APIRouter(tags=["mock-results"])

MAX_FILES = 10


def serialize_mock_result(result: MockResult) -> dict:
    attachments = read_attachments(result.attachments)
    payload = MockResultSchema(
        id=result.id,
        assignment_id=result.assignment_id,
        student_id=result.student_id,
        submitted=result.submitted,
        total_points=result.total_points,
        verbal_points=result.verbal_points,
        math_points=result.math_points,
        verbal_incorrect=result.verbal_incorrect,
        math_incorrect=result.math_incorrect,
        weak_areas=result.weak_areas,
        photo_link=result.photo_link,
        attachments=[
            MockFileSchema.model_validate(item)
            for item in attachment_dicts_to_api(attachments)
        ],
        legacy_photo=len(attachments) == 0 and result.photo_link is not None,
    )
    return payload.model_dump(mode="json")


def serialize_mock_result_list_item(result: MockResult) -> dict:
    serialized = serialize_mock_result(result)
    serialized["result_id"] = serialized.pop("id")
    return serialized


def get_mock_result_or_404(result_id: int, db: Session) -> MockResult:
    result = db.query(MockResult).filter(MockResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Mock result not found")
    return result


@router.get("/mock-results/{result_id}", response_model=MockResultSchema)
def get_mock_result(
    result_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["admin", "teacher", "student"])),
):
    result = get_mock_result_or_404(result_id, db)
    if current_user.role == "student" and current_user.id != result.student_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    return serialize_mock_result(result)


@router.post("/mock-results/{result_id}/upload")
async def upload_mock_result_files(
    result_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["student"])),
):
    result = get_mock_result_or_404(result_id, db)
    if current_user.id != result.student_id:
        raise HTTPException(
            status_code=403,
            detail="Students can upload only for their own mock results",
        )
    if result.submitted:
        raise HTTPException(
            status_code=409,
            detail="Cannot upload files to an already submitted mock result",
        )

    existing = read_attachments(result.attachments)
    if len(existing) + len(files) > MAX_FILES:
        return JSONResponse(
            status_code=422,
            content={
                "error": "TOO_MANY_FILES",
                "detail": "Maximum 10 files per submission",
                "max": MAX_FILES,
            },
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
                    folder="mock",
                    public_id_prefix="mock",
                )
            )
    except HTTPException:
        for uploaded in uploaded_cloudinary:
            delete_file(uploaded["public_id"], uploaded["content_type"])
        raise
    except Exception:
        for uploaded in uploaded_cloudinary:
            delete_file(uploaded["public_id"], uploaded["content_type"])
        raise HTTPException(status_code=500, detail="Upload failed, all files rolled back")

    try:
        write_attachments(result, append_uploads(existing, uploaded_cloudinary))
        if result.photo_link is None:
            first_image = first_image_url_from_uploads(uploaded_cloudinary)
            if first_image is not None:
                result.photo_link = first_image

        db.commit()
        db.refresh(result)
    except Exception:
        db.rollback()
        for uploaded in uploaded_cloudinary:
            delete_file(uploaded["public_id"], uploaded["content_type"])
        raise HTTPException(status_code=500, detail="Upload failed, all files rolled back")

    return serialize_mock_result(result)
