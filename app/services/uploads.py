from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import BASE_DIR


ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".heic", ".heif"}
ALLOWED_IMAGE_MESSAGE = "Поддерживаются изображения PNG, JPG, WEBP, GIF, SVG, HEIC или HEIF."


def save_avatar(upload: UploadFile | None) -> str | None:
    if not upload or not upload.filename:
        return None

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError(ALLOWED_IMAGE_MESSAGE)

    target_dir = BASE_DIR / "app" / "static" / "uploads" / "avatars"
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid4().hex}{suffix}"
    target_path = target_dir / filename
    payload = upload.file.read()
    target_path.write_bytes(payload)
    return f"/static/uploads/avatars/{filename}"


def save_receipt_photo(upload: UploadFile | None) -> str | None:
    if not upload or not upload.filename:
        return None

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError(ALLOWED_IMAGE_MESSAGE)

    target_dir = BASE_DIR / "app" / "static" / "uploads" / "receipts"
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid4().hex}{suffix}"
    target_path = target_dir / filename
    payload = upload.file.read()
    target_path.write_bytes(payload)
    return f"/static/uploads/receipts/{filename}"


def save_prepayment_photo(upload: UploadFile | None) -> str | None:
    if not upload or not upload.filename:
        return None

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError(ALLOWED_IMAGE_MESSAGE)

    target_dir = BASE_DIR / "app" / "static" / "uploads" / "prepayments"
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid4().hex}{suffix}"
    target_path = target_dir / filename
    payload = upload.file.read()
    target_path.write_bytes(payload)
    return f"/static/uploads/prepayments/{filename}"
