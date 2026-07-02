from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from dependencies.auth import is_admin_or_mentor
from Methods.auth import get_db, get_current_user, normalize_role
from models import MockResult, User
from services.attachments import (
    find_attachment_by_id,
    first_image_url,
    read_attachments,
    remove_attachment_at_index,
)
from services.cloudinary_service import delete_file

router = APIRouter(tags=["mock-files"])
logger = logging.getLogger(__name__)


@router.delete("/mock-files/{file_id}", status_code=204)
def delete_mock_file(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    results = db.query(MockResult).all()
    result, index, row = find_attachment_by_id(results, file_id)
    if result is None or index is None or row is None:
        raise HTTPException(status_code=404, detail="Mock file not found")

    role = normalize_role(current_user.role)
    if is_admin_or_mentor(role):
        pass
    elif role == "student":
        if current_user.id != result.student_id:
            raise HTTPException(status_code=403, detail="Not enough permissions")
        if result.submitted:
            raise HTTPException(
                status_code=403,
                detail="Cannot delete files from a submitted mock result",
            )
    else:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    deleted_url = row.get("url")
    if row.get("public_id") is not None:
        delete_file(row["public_id"], row.get("content_type", "image/jpeg"))

    remove_attachment_at_index(result, index)

    if result.photo_link == deleted_url:
        remaining = read_attachments(result.attachments)
        result.photo_link = first_image_url(remaining)

    db.commit()
    return Response(status_code=204)
