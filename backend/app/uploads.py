import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.config import settings

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _matches_signature(content: bytes, content_type: str) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


async def save_upload(file: UploadFile, subdir: str) -> str:
    ext = ALLOWED_CONTENT_TYPES.get(file.content_type)
    if ext is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Допустимы только изображения JPEG, PNG или WebP",
        )

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    # Read at most one byte past the limit so a huge upload can't be pulled fully into memory.
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Файл слишком большой (максимум {settings.max_upload_size_mb} МБ)",
        )
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой файл")
    # The Content-Type header is set by the client, so check the file's real signature too:
    # otherwise an HTML/SVG file could be stored and served from our domain as an "image".
    if not _matches_signature(content, file.content_type):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл не похож на изображение JPEG, PNG или WebP")

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
