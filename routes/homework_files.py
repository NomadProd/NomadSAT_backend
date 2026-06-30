from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from Methods.auth import get_db, get_current_user, normalize_role
from models import HomeworkFile, HomeworkResult, User
from services.cloudinary_service import delete_file

router = APIRouter(tags=["homework-files"])
logger = logging.getLogger(__name__)


@router.delete("/homework-files/{file_id}", status_code=204)
def delete_homework_file(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if normalize_role(current_user.role) != "admin":
        raise HTTPException(status_code=403, detail="Not enough permissions")

    row = db.query(HomeworkFile).filter(HomeworkFile.id == file_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Homework file not found")

    deleted_url = row.url
    result_id = row.result_id

    if row.public_id is not None:
        delete_file(row.public_id, row.content_type)

    db.delete(row)
    db.flush()

    result = db.query(HomeworkResult).filter(HomeworkResult.id == result_id).first()
    if result is not None and result.photo_link == deleted_url:
        next_image = (
            db.query(HomeworkFile)
            .filter(
                HomeworkFile.result_id == result_id,
                HomeworkFile.content_type.like("image/%"),
            )
            .order_by(HomeworkFile.uploaded_at.asc())
            .first()
        )
        result.photo_link = next_image.url if next_image is not None else None

    db.commit()
    return Response(status_code=204)
