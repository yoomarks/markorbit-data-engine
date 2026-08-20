from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import os
from pathlib import Path

from PIL import Image, ImageChops, ImageOps


MAX_IMAGE_BYTES = 25 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000


@dataclass(frozen=True)
class RawJpegAnalysis:
    raw_sha256: str
    pixel_sha256: str
    byte_size: int
    width: int
    height: int
    content_bbox: tuple[int, int, int, int] | None
    dhash64: str
    exif_orientation: int


@dataclass(frozen=True)
class CanonicalJpeg:
    data: bytes
    sha256: str
    byte_size: int
    width: int
    height: int
    transformed: bool


def _content_bbox(rgb: Image.Image) -> tuple[int, int, int, int] | None:
    white = Image.new("RGB", rgb.size, (255, 255, 255))
    diff = ImageChops.difference(rgb, white).convert("L")
    mask = diff.point(lambda value: 255 if value > 4 else 0)
    bbox = mask.getbbox()
    return tuple(int(value) for value in bbox) if bbox else None


def _dhash64(rgb: Image.Image, bbox: tuple[int, int, int, int] | None) -> str:
    image = rgb.crop(bbox) if bbox else rgb
    gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    value = 0
    for row in range(8):
        base = row * 9
        for column in range(8):
            value <<= 1
            value |= int(pixels[base + column] > pixels[base + column + 1])
    return f"{value:016x}"


def _open_jpeg(raw_bytes: bytes) -> tuple[Image.Image, int]:
    if not raw_bytes:
        raise ValueError("empty CN mark image")
    if len(raw_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(f"CN mark image exceeds {MAX_IMAGE_BYTES} bytes")

    source = Image.open(BytesIO(raw_bytes))
    try:
        source.load()
        if str(source.format or "").upper() != "JPEG":
            raise ValueError(
                f"CN official mark image must be JPEG, got {source.format or 'UNKNOWN'}"
            )
        orientation = int(source.getexif().get(274, 1) or 1)
        image = ImageOps.exif_transpose(source).convert("RGB")
    finally:
        source.close()

    width, height = image.size
    if width < 1 or height < 1:
        image.close()
        raise ValueError("CN mark image has invalid dimensions")
    return image, orientation


def analyze_jpeg(raw_bytes: bytes) -> RawJpegAnalysis:
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    image, orientation = _open_jpeg(raw_bytes)
    try:
        width, height = image.size
        pixel_hasher = hashlib.sha256()
        pixel_hasher.update(f"RGB:{width}x{height}:".encode("ascii"))
        pixel_hasher.update(image.tobytes())
        bbox = _content_bbox(image)
        dhash64 = _dhash64(image, bbox)
        return RawJpegAnalysis(
            raw_sha256=raw_sha256,
            pixel_sha256=pixel_hasher.hexdigest(),
            byte_size=len(raw_bytes),
            width=int(width),
            height=int(height),
            content_bbox=bbox,
            dhash64=dhash64,
            exif_orientation=orientation,
        )
    finally:
        image.close()


def canonicalize_jpeg(
    raw_bytes: bytes,
    *,
    max_edge: int = 1600,
    passthrough_max_bytes: int = 256 * 1024,
    quality: int = 90,
) -> CanonicalJpeg:
    if max_edge < 256:
        raise ValueError("max_edge must be >= 256")
    if passthrough_max_bytes < 0:
        raise ValueError("passthrough_max_bytes must be >= 0")
    if not 75 <= quality <= 95:
        raise ValueError("quality must be between 75 and 95")

    image, orientation = _open_jpeg(raw_bytes)
    try:
        width, height = image.size
        needs_resize = max(width, height) > max_edge
        needs_reencode = len(raw_bytes) > passthrough_max_bytes or orientation != 1
        if not needs_resize and not needs_reencode:
            digest = hashlib.sha256(raw_bytes).hexdigest()
            return CanonicalJpeg(
                data=raw_bytes,
                sha256=digest,
                byte_size=len(raw_bytes),
                width=int(width),
                height=int(height),
                transformed=False,
            )

        if needs_resize:
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        output = BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=False,
            subsampling=1,
        )
        candidate = output.getvalue()

        if not needs_resize and orientation == 1 and len(candidate) >= len(raw_bytes):
            candidate = raw_bytes
            transformed = False
            canonical_width, canonical_height = width, height
        else:
            transformed = True
            canonical_width, canonical_height = image.size

        return CanonicalJpeg(
            data=candidate,
            sha256=hashlib.sha256(candidate).hexdigest(),
            byte_size=len(candidate),
            width=int(canonical_width),
            height=int(canonical_height),
            transformed=transformed,
        )
    finally:
        image.close()


def content_addressed_key(*, tier: str, sha256: str) -> Path:
    if tier not in {"raw", "processed"}:
        raise ValueError("tier must be raw or processed")
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise ValueError("sha256 must be a lowercase 64-character hex digest")
    return (
        Path("assets")
        / tier
        / "cn"
        / "mark-images"
        / sha256[:2]
        / sha256[2:4]
        / f"{sha256}.jpg"
    )


def store_content_addressed(
    data: bytes,
    *,
    root: Path,
    relative_key: Path,
) -> str:
    root = root.resolve()
    path = root / relative_key
    if path.exists():
        return relative_key.as_posix()

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return relative_key.as_posix()
