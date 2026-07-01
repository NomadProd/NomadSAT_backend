from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from Methods.auth import get_db, get_current_user, normalize_role
from models import HomeworkResult, User
from services.attachments import (
    all_archived_public_ids,
    find_attachment_by_id,
    first_image_url,
    read_attachments,
    remove_attachment_at_index,
)
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

    results = db.query(HomeworkResult).all()
    result, index, row = find_attachment_by_id(results, file_id)
    if result is None or index is None or row is None:
        raise HTTPException(status_code=404, detail="Homework file not found")

    public_id = row.get("public_id")
    archived_ids = all_archived_public_ids(result)
    in_archive = public_id is not None and str(public_id) in archived_ids
    is_active_submission = result.submitted and result.returned_at is None

    if in_archive and not is_active_submission:
        raise HTTPException(
            status_code=403,
            detail="Cannot delete archived submission files",
        )

    deleted_url = row.get("url")
    if public_id is not None and (is_active_submission or not in_archive):
        delete_file(public_id, row.get("content_type", "image/jpeg"))

    remove_attachment_at_index(result, index)

    if result.photo_link == deleted_url:
        remaining = read_attachments(result.attachments)
        result.photo_link = first_image_url(remaining)

    db.commit()
    return Response(status_code=204)
