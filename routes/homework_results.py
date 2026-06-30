from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
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
    Class,
    HomeworkFile,
    HomeworkResult,
    Session as ClassSession,
    User,
)
from schemas.homework_result import HomeworkFileSchema, HomeworkResultSchema, ReturnHomeworkRequest
from services.cloudinary_service import delete_file, upload_file

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
ALLOWED_CONTENT_TYPES_DISPLAY = ["image/*", "application/pdf"]


def validation_error(status_code: int, payload: dict) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


def conflict_error(payload: dict) -> JSONResponse:
    return JSONResponse(status_code=409, content=payload)


def first_image_url(uploaded_files: list[dict]) -> str | None:
    for uploaded in uploaded_files:
        if uploaded["content_type"].startswith("image/"):
            return uploaded["url"]
    return None


def replace_result_files(
    db: Session,
    result_id: int,
    uploaded_cloudinary: list[dict],
) -> list[HomeworkFile]:
    old_files = (
        db.query(HomeworkFile)
        .filter(HomeworkFile.result_id == result_id)
        .all()
    )
    for old_file in old_files:
        if old_file.public_id is not None:
            delete_file(old_file.public_id, old_file.content_type)

    db.query(HomeworkFile).filter(HomeworkFile.result_id == result_id).delete()

    new_rows: list[HomeworkFile] = []
    for uploaded in uploaded_cloudinary:
        row = HomeworkFile(
            result_id=result_id,
            url=uploaded["url"],
            public_id=uploaded["public_id"],
            filename=uploaded["filename"],
            content_type=uploaded["content_type"],
            size_bytes=uploaded["size_bytes"],
            uploaded_at=datetime.utcnow(),
        )
        db.add(row)
        new_rows.append(row)
    return new_rows


def detect_magic_content_type(header: bytes) -> str | None:
    if len(header) < 4:
        return None
    if header[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if header[:4] == b"\x89PNG":
        return "image/png"
    if header[:4] == b"GIF8":
        return "image/gif"
    if header[:4] == b"RIFF" and len(header) >= 12 and header[8:12] == b"WEBP":
        return "image/webp"
    if header[:4] == b"%PDF":
        return "application/pdf"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "image/heic"
    return None


def content_type_matches_magic(declared_type: str, magic_type: str | None) -> bool:
    if magic_type is None:
        return False
    if declared_type == magic_type:
        return True
    if declared_type == "image/heic" and magic_type == "image/heic":
        return True
    if declared_type == "image/jpeg" and magic_type == "image/jpeg":
        return True
    return False


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


def can_view_homework_result(current_user: User, assignment: Assignment, db: Session) -> bool:
    if current_user.role == "admin":
        return True
    if current_user.role == "student":
        return current_user.id == assignment.student_id
    if current_user.role == "teacher":
        session_obj = db.query(ClassSession).filter(
            ClassSession.id == assignment.session_id
        ).first()
        if not session_obj:
            return False
        class_obj = db.query(Class).filter(Class.id == session_obj.class_id).first()
        if not class_obj:
            return False
        return current_user.id in {class_obj.verbal_teacher_id, class_obj.math_teacher_id}
    return False


def load_attachments(result_id: int, db: Session) -> list[HomeworkFile]:
    return (
        db.query(HomeworkFile)
        .filter(HomeworkFile.result_id == result_id)
        .order_by(HomeworkFile.uploaded_at.asc())
        .all()
    )


def serialize_homework_result(
    result: HomeworkResult,
    attachments: list[HomeworkFile],
) -> dict:
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
        attachments=[HomeworkFileSchema.model_validate(item) for item in attachments],
        legacy_photo=len(attachments) == 0 and result.photo_link is not None,
    )
    return payload.model_dump(mode="json")


async def validate_upload_files(files: list[UploadFile]) -> list[dict]:
    if not files:
        raise HTTPException(status_code=422, detail="At least one file is required")

    if len(files) > MAX_FILES:
        return validation_error(
            422,
            {
                "error": "TOO_MANY_FILES",
                "detail": "Maximum 10 files per submission",
                "max": MAX_FILES,
            },
        )

    validated: list[dict] = []
    for upload in files:
        filename = upload.filename or "upload"
        declared_type = (upload.content_type or "").strip().lower()
        if declared_type not in ALLOWED_CONTENT_TYPES:
            return validation_error(
                422,
                {
                    "error": "INVALID_FILE_TYPE",
                    "detail": f"File {filename} has unsupported type",
                    "filename": filename,
                    "allowed": ALLOWED_CONTENT_TYPES_DISPLAY,
                },
            )

        file_bytes = await upload.read()
        if len(file_bytes) > MAX_FILE_SIZE_BYTES:
            return validation_error(
                422,
                {
                    "error": "FILE_TOO_LARGE",
                    "detail": f"File {filename} exceeds 50 MB",
                    "filename": filename,
                    "max_mb": 50,
                },
            )

        magic_type = detect_magic_content_type(file_bytes[:12])
        if not content_type_matches_magic(declared_type, magic_type):
            return validation_error(
                422,
                {
                    "error": "INVALID_FILE_TYPE",
                    "detail": f"File {filename} has unsupported type",
                    "filename": filename,
                    "allowed": ALLOWED_CONTENT_TYPES_DISPLAY,
                },
            )

        validated.append(
            {
                "filename": filename,
                "content_type": declared_type,
                "bytes": file_bytes,
            }
        )

    return validated


@router.get("/homework-results")
def list_homework_results(
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    results = homework_results_query(db, current_user).order_by(HomeworkResult.id.asc()).all()
    payload = []
    for result in results:
        attachments = load_attachments(result.id, db)
        payload.append(serialize_homework_result(result, attachments))
    return payload


@router.get("/homework-results/{result_id}", response_model=HomeworkResultSchema)
def get_homework_result(
    result_id: int,
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

    attachments = load_attachments(result_id, db)
    return serialize_homework_result(result, attachments)


@router.post("/homework-results/{result_id}/return")
def return_homework_for_revision(
    result_id: int,
    body: ReturnHomeworkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(legacy_get_current_user),
):
    if normalize_role(current_user.role) != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")

    result = get_homework_result_or_404(result_id, db)

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
    result.submitted = False
    result.submitted_at = None
    result.returned_at = datetime.utcnow()
    result.returned_by_id = current_user.id
    result.return_reason = reason
    result.correct_total = None
    result.incorrect_total = None
    result.analysis = None

    db.commit()
    db.refresh(result)

    attachments = load_attachments(result_id, db)
    return serialize_homework_result(result, attachments)


@router.post("/homework-results/{result_id}/upload")
async def upload_homework_result_files(
    result_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(legacy_require_roles(["student"])),
):
    result = get_homework_result_or_404(result_id, db)
    assignment = get_assignment_for_result(result, db)

    if current_user.id != assignment.student_id:
        raise HTTPException(
            status_code=403,
            detail="Students can upload only for their own homework results",
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
        for uploaded in uploaded_cloudinary:
            delete_file(uploaded["public_id"], uploaded["content_type"])
        raise
    except Exception:
        for uploaded in uploaded_cloudinary:
            delete_file(uploaded["public_id"], uploaded["content_type"])
        raise HTTPException(status_code=500, detail="Upload failed, all files rolled back")

    try:
        is_resubmission = result.returned_at is not None

        if is_resubmission:
            new_rows = replace_result_files(db, result_id, uploaded_cloudinary)
            first_image = first_image_url(uploaded_cloudinary)
            if first_image is not None:
                result.photo_link = first_image

            result.submitted = True
            result.submitted_at = datetime.utcnow()
            result.returned_at = None
            result.returned_by_id = None
            result.return_reason = None
        else:
            new_rows: list[HomeworkFile] = []
            for uploaded in uploaded_cloudinary:
                row = HomeworkFile(
                    result_id=result_id,
                    url=uploaded["url"],
                    public_id=uploaded["public_id"],
                    filename=uploaded["filename"],
                    content_type=uploaded["content_type"],
                    size_bytes=uploaded["size_bytes"],
                    uploaded_at=datetime.utcnow(),
                )
                db.add(row)
                new_rows.append(row)

            if result.photo_link is None:
                first_image = first_image_url(uploaded_cloudinary)
                if first_image is not None:
                    result.photo_link = first_image

        db.commit()
        for row in new_rows:
            db.refresh(row)
        db.refresh(result)
    except Exception:
        db.rollback()
        for uploaded in uploaded_cloudinary:
            delete_file(uploaded["public_id"], uploaded["content_type"])
        raise HTTPException(status_code=500, detail="Upload failed, all files rolled back")

    all_attachments = load_attachments(result_id, db)
    return serialize_homework_result(result, all_attachments)
