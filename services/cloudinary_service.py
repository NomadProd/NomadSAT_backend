from __future__ import annotations

import logging
from uuid import uuid4

import cloudinary
import cloudinary.uploader
from fastapi import HTTPException

from config import env

logger = logging.getLogger(__name__)


def _configure_cloudinary() -> None:
    cloud_name = env("CLOUDINARY_CLOUD_NAME")
    api_key = env("CLOUDINARY_API_KEY")
    api_secret = env("CLOUDINARY_API_SECRET")
    if not cloud_name or not api_key or not api_secret:
        raise HTTPException(
            status_code=500,
            detail=(
                "Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."
            ),
        )
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )


def upload_file(
    file_bytes: bytes,
    *,
    result_id: int,
    filename: str,
    content_type: str,
    folder: str = "homework",
    public_id_prefix: str = "homework",
) -> dict[str, str | int]:
    _configure_cloudinary()
    is_pdf = content_type == "application/pdf"
    resource_type = "raw" if is_pdf else "image"
    public_id = f"{public_id_prefix}_{result_id}_{uuid4().hex[:8]}"
    if is_pdf:
        public_id = f"{public_id}.pdf"
    try:
        result = cloudinary.uploader.upload(
            file_bytes,
            folder=folder,
            resource_type=resource_type,
            public_id=public_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Upload failed, rolled back",
        ) from exc

    secure_url = result.get("secure_url")
    public_id = result.get("public_id")
    if not secure_url or not public_id:
        raise HTTPException(
            status_code=500,
            detail="Upload failed, rolled back",
        )

    return {
        "url": secure_url,
        "public_id": public_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(file_bytes),
    }


def rollback_uploads(entries: list[dict]) -> None:
    for entry in entries:
        public_id = entry.get("public_id")
        if public_id:
            delete_file(public_id, entry.get("content_type", "image/jpeg"))


def delete_file(public_id: str, content_type: str) -> None:
    """Delete a Cloudinary asset. Logs errors but does not raise."""
    _configure_cloudinary()
    resource_type = "raw" if content_type == "application/pdf" else "image"
    try:
        cloudinary.uploader.destroy(public_id, resource_type=resource_type)
    except Exception as exc:
        logger.error(
            "Cloudinary delete failed for public_id=%s: %s",
            public_id,
            exc,
            exc_info=True,
        )


def delete_attachments_best_effort(entries: list[dict]) -> None:
    """Delete multiple Cloudinary assets; failures are logged and skipped."""
    for entry in entries:
        public_id = entry.get("public_id")
        if public_id:
            delete_file(public_id, entry.get("content_type", "image/jpeg"))
