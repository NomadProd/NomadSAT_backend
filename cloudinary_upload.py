import hashlib
import http.client
import json
import mimetypes
import ssl
import time
import uuid
from urllib.parse import quote, urlparse

import certifi
from fastapi import HTTPException, UploadFile

from config import env


def _https_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _multipart_body(fields: dict[str, str], file: UploadFile, data: bytes) -> tuple[bytes, str]:
    boundary = f"----TuranCloudinary{uuid.uuid4().hex}"
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )

    filename = quote(file.filename or "homework-upload", safe="")
    content_type = (
        file.content_type
        or mimetypes.guess_type(file.filename or "")[0]
        or "application/octet-stream"
    )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def _cloudinary_config() -> tuple[str, str, str]:
    cloud_name = env("CLOUDINARY_CLOUD_NAME")
    api_key = env("CLOUDINARY_API_KEY")
    api_secret = env("CLOUDINARY_API_SECRET")

    cloudinary_url = env("CLOUDINARY_URL")
    if cloudinary_url and (not cloud_name or not api_key or not api_secret):
        parsed = urlparse(cloudinary_url)
        if parsed.scheme == "cloudinary":
            api_key = api_key or parsed.username or ""
            api_secret = api_secret or parsed.password or ""
            cloud_name = cloud_name or parsed.hostname or ""

    if not cloud_name or not api_key or not api_secret:
        raise HTTPException(
            status_code=500,
            detail=(
                "Cloudinary is not configured. Add CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET to backend/.env, "
                "or set CLOUDINARY_URL."
            ),
        )

    return cloud_name, api_key, api_secret


async def upload_homework_file(file: UploadFile, *, assignment_id: int) -> str:
    cloud_name, api_key, api_secret = _cloudinary_config()

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    timestamp = str(int(time.time()))
    folder = f"turan/homework/{assignment_id}"
    signature_payload = f"folder={folder}&timestamp={timestamp}{api_secret}"
    signature = hashlib.sha1(signature_payload.encode("utf-8")).hexdigest()

    fields = {
        "api_key": api_key,
        "timestamp": timestamp,
        "folder": folder,
        "signature": signature,
    }
    body, boundary = _multipart_body(fields, file, data)

    conn = http.client.HTTPSConnection(
        "api.cloudinary.com",
        timeout=30,
        context=_https_context(),
    )
    try:
        conn.request(
            "POST",
            f"/v1_1/{cloud_name}/auto/upload",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        response = conn.getresponse()
        response_body = response.read().decode("utf-8")
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"Cloudinary upload failed: {exc}") from exc
    finally:
        conn.close()

    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Cloudinary returned an invalid response") from exc

    if response.status >= 400:
        message = payload.get("error", {}).get("message") or "Cloudinary upload failed"
        raise HTTPException(status_code=502, detail=message)

    secure_url = payload.get("secure_url")
    if not secure_url:
        raise HTTPException(status_code=502, detail="Cloudinary did not return a file URL")

    return secure_url
