from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.cloudinary_service import delete_file


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_uploaded_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_attachments(raw: Any) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    return []


def write_attachments(result: Any, items: list[dict]) -> None:
    result.attachments = items


def next_attachment_id(items: list[dict]) -> int:
    ids = [int(item["id"]) for item in items if item.get("id") is not None]
    return (max(ids) if ids else 0) + 1


def attachment_from_upload(uploaded: dict) -> dict:
    return {
        "url": uploaded["url"],
        "public_id": uploaded.get("public_id"),
        "filename": uploaded["filename"],
        "content_type": uploaded["content_type"],
        "size_bytes": uploaded["size_bytes"],
        "uploaded_at": uploaded.get("uploaded_at") or _utc_now_iso(),
    }


def append_uploads(existing: list[dict], uploaded_items: list[dict]) -> list[dict]:
    items = [dict(item) for item in existing]
    for uploaded in uploaded_items:
        row = attachment_from_upload(uploaded)
        row["id"] = next_attachment_id(items)
        items.append(row)
    return items


def replace_with_uploads(uploaded_items: list[dict]) -> list[dict]:
    items: list[dict] = []
    for uploaded in uploaded_items:
        row = attachment_from_upload(uploaded)
        row["id"] = next_attachment_id(items)
        items.append(row)
    return items


def delete_cloudinary_files(items: list[dict]) -> None:
    for item in items:
        public_id = item.get("public_id")
        if public_id:
            delete_file(public_id, item.get("content_type", "image/jpeg"))


def find_attachment_by_id(
    results: list[Any],
    file_id: int,
) -> tuple[Any | None, int | None, dict | None]:
    for result in results:
        items = read_attachments(result.attachments)
        for index, item in enumerate(items):
            if int(item.get("id", -1)) == file_id:
                return result, index, item
    return None, None, None


def remove_attachment_at_index(result: Any, index: int) -> dict | None:
    items = read_attachments(result.attachments)
    if index < 0 or index >= len(items):
        return None
    removed = items.pop(index)
    write_attachments(result, items)
    return removed


def first_image_url(items: list[dict]) -> str | None:
    for item in items:
        content_type = str(item.get("content_type", ""))
        if content_type.startswith("image/"):
            return item.get("url")
    return None


def first_image_url_from_uploads(uploaded_items: list[dict]) -> str | None:
    for uploaded in uploaded_items:
        if uploaded["content_type"].startswith("image/"):
            return uploaded["url"]
    return None


def attachment_dicts_to_api(items: list[dict]) -> list[dict]:
    payload: list[dict] = []
    for item in items:
        payload.append(
            {
                "id": int(item.get("id", 0)),
                "url": item["url"],
                "filename": item["filename"],
                "content_type": item["content_type"],
                "size_bytes": int(item.get("size_bytes", 0)),
                "uploaded_at": parse_uploaded_at(item.get("uploaded_at")),
            }
        )
    return payload
