import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.config import settings

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


async def save_upload(file: UploadFile, subdir: str) -> str:
    ext = ALLOWED_CONTENT_TYPES.get(file.content_type)
    if ext is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Допустимы только изображения JPEG, PNG или WebP",
        )

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Файл слишком большой (максимум {settings.max_upload_size_mb} МБ)",
        )
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой файл")

    directory = Path(settings.upload_dir) / subdir
    directory.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{ext}"
    (directory / filename).write_bytes(content)

    return f"/uploads/{subdir}/{filename}"


def delete_upload(url: str) -> None:
    if not url.startswith("/uploads/"):
        return
    base = Path(settings.upload_dir).resolve()
    path = (base / url.removeprefix("/uploads/")).resolve()
    if base not in path.parents:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
