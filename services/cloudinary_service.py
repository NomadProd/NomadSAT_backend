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
) -> dict[str, str | int]:
    _configure_cloudinary()
    try:
        result = cloudinary.uploader.upload(
            file_bytes,
            folder="homework",
            resource_type="auto",
            public_id=f"homework_{result_id}_{uuid4().hex[:8]}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Upload failed, all files rolled back",
        ) from exc

    secure_url = result.get("secure_url")
    public_id = result.get("public_id")
    if not secure_url or not public_id:
        raise HTTPException(
            status_code=500,
            detail="Upload failed, all files rolled back",
        )

    return {
        "url": secure_url,
        "public_id": public_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(file_bytes),
    }


def delete_file(public_id: str, content_type: str) -> None:
    _configure_cloudinary()
    resource_type = "raw" if content_type == "application/pdf" else "image"
    try:
        cloudinary.uploader.destroy(public_id, resource_type=resource_type)
    except Exception as exc:
        logger.warning(
            "Cloudinary delete failed for public_id=%s: %s",
            public_id,
            exc,
        )
