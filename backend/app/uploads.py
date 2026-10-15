import io
import uuid
import warnings
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageOps

from app.config import settings

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


# A decompression bomb (a small file that expands to gigapixels) is refused before it is decoded.
# 60 MP still lets through photos of 50-megapixel phones.
Image.MAX_IMAGE_PIXELS = 60_000_000
MAX_SIDE = 2560  # bigger photos are scaled down: nobody needs 8000px to show a leaking tap
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def clean_image(content: bytes, content_type: str) -> bytes:
    """Decode the picture completely and save it again from its pixels.

    Only real images of the declared type survive (a polyglot HTML/JS file or a broken image is refused),
    and nothing but the pixels is kept: EXIF with GPS coordinates, XMP, comments and trailing data are gone.
    The EXIF rotation is applied to the pixels first, so phone photos stay upright.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            return _reencode(content, content_type)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Изображение слишком большое") from None
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл не похож на изображение JPEG, PNG или WebP") from None


def _reencode(content: bytes, content_type: str) -> bytes:
    with Image.open(io.BytesIO(content)) as probe:
        if probe.format != FORMATS[content_type]:
            raise ValueError("format mismatch")
        probe.verify()  # structure and checksums
    with Image.open(io.BytesIO(content)) as img:
        if img.format == "JPEG":
            img.draft("RGB", (MAX_SIDE, MAX_SIDE))  # decode a big JPEG straight at a smaller scale: less memory
        img.load()  # the full decode
        img = ImageOps.exif_transpose(img)
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        if content_type == "image/jpeg":
            img.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
        elif content_type == "image/png":
            if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                img = img.convert("RGBA")
            img.save(out, "PNG", optimize=True)
        else:
            img.convert("RGBA" if "A" in img.getbands() else "RGB").save(out, "WEBP", quality=88)
        return out.getvalue()


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
    # The Content-Type header is set by the client: only a file that really decodes as that image type is
    # stored, re-encoded, so an HTML/SVG file can't be served from our domain as an "image".
    content = clean_image(content, file.content_type)

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
