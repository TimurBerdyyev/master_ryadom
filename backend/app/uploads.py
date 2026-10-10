import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.config import settings

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _strip_jpeg(content: bytes) -> bytes:
    """Drop APP1..APP15 segments (EXIF with GPS coordinates, XMP, maker notes) and comments; keep the image."""
    out = bytearray(content[:2])
    i = 2
    while i + 4 <= len(content):
        if content[i] != 0xFF:
            return content  # not a well-formed JPEG: leave it as is
        marker = content[i + 1]
        if marker == 0xDA:  # start of scan: the rest is image data
            out += content[i:]
            return bytes(out)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # markers without a length
            out += content[i:i + 2]
            i += 2
            continue
        length = int.from_bytes(content[i + 2:i + 4], "big")
        segment = content[i:i + 2 + length]
        # APP0 (JFIF) and APP14 (Adobe colour info) are needed; ICC profile lives in APP2 — keep it too.
        is_metadata = (0xE1 <= marker <= 0xEF and marker not in (0xE2, 0xEE)) or marker == 0xFE
        if not is_metadata:
            out += segment
        i += 2 + length
    return content


def _strip_png(content: bytes) -> bytes:
    """Drop eXIf and text chunks (tEXt/zTXt/iTXt can hold location and device data)."""
    out = bytearray(content[:8])
    i = 8
    while i + 12 <= len(content):
        length = int.from_bytes(content[i:i + 4], "big")
        chunk_type = content[i + 4:i + 8]
        chunk = content[i:i + 12 + length]
        if len(chunk) < 12 + length:
            return content  # truncated chunk: don't risk breaking the image
        if chunk_type not in (b"eXIf", b"tEXt", b"zTXt", b"iTXt"):
            out += chunk
        i += 12 + length
        if chunk_type == b"IEND":
            return bytes(out)
    return content  # no IEND: not a well-formed PNG, leave it as is


def _strip_webp(content: bytes) -> bytes:
    """Drop EXIF/XMP chunks of an extended WebP and clear their flags in the VP8X header."""
    if content[12:16] != b"VP8X":
        return content  # simple WebP has no metadata chunks
    chunks = bytearray()
    i = 12
    while i + 8 <= len(content):
        chunk_type = content[i:i + 4]
        size = int.from_bytes(content[i + 4:i + 8], "little")
        chunk = bytearray(content[i:i + 8 + size + (size & 1)])
        if len(chunk) < 8 + size:
            return content  # truncated chunk: leave the file as is
        if chunk_type == b"VP8X":
            chunk[8] &= ~0x0C & 0xFF  # EXIF (0x08) and XMP (0x04) flags
        if chunk_type not in (b"EXIF", b"XMP "):
            chunks += chunk
        i += 8 + size + (size & 1)
    return b"RIFF" + (len(chunks) + 4).to_bytes(4, "little") + b"WEBP" + bytes(chunks)


def strip_metadata(content: bytes, content_type: str) -> bytes:
    """Phone photos carry EXIF with GPS coordinates: a photo of a leaking tap would reveal the client's home."""
    try:
        if content_type == "image/jpeg":
            return _strip_jpeg(content)
        if content_type == "image/png":
            return _strip_png(content)
        if content_type == "image/webp":
            return _strip_webp(content)
    except Exception:
        pass
    return content


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

    content = strip_metadata(content, file.content_type)

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
