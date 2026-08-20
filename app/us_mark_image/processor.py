from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import uuid

from PIL import Image, ImageChops

from app.config import get_settings
from app.db import postgres_conn
from app.us_mark_image.migrations import ensure_mark_image_schema


MAX_IMAGE_BYTES = 10 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000

_FORMAT_EXTENSION = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "GIF": ".gif",
    "TIFF": ".tif",
    "WEBP": ".webp",
    "BMP": ".bmp",
}


@dataclass(frozen=True)
class ImageAnalysis:
    sha256: str
    mime_type: str
    file_extension: str
    byte_size: int
    width: int
    height: int
    content_bbox: tuple[int, int, int, int] | None
    dhash64: str


def _flatten_on_white(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def _content_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    rgb = _flatten_on_white(image)
    white = Image.new("RGB", rgb.size, (255, 255, 255))
    diff = ImageChops.difference(rgb, white).convert("L")
    mask = diff.point(lambda value: 255 if value > 4 else 0)
    bbox = mask.getbbox()
    return tuple(int(value) for value in bbox) if bbox else None


def _dhash64(image: Image.Image, bbox: tuple[int, int, int, int] | None) -> str:
    rgb = _flatten_on_white(image)
    if bbox:
        rgb = rgb.crop(bbox)
    gray = rgb.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    value = 0
    for row in range(8):
        base = row * 9
        for column in range(8):
            value <<= 1
            value |= int(pixels[base + column] > pixels[base + column + 1])
    return f"{value:016x}"


def analyze_image(raw_bytes: bytes) -> ImageAnalysis:
    if not raw_bytes:
        raise ValueError("empty mark image")
    if len(raw_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(f"mark image exceeds {MAX_IMAGE_BYTES} bytes")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    with Image.open(BytesIO(raw_bytes)) as image:
        image.load()
        image_format = str(image.format or "").upper()
        extension = _FORMAT_EXTENSION.get(image_format)
        if not extension:
            raise ValueError(f"unsupported mark image format: {image_format or 'UNKNOWN'}")
        mime_type = Image.MIME.get(image_format, "application/octet-stream")
        width, height = image.size
        if width < 1 or height < 1:
            raise ValueError("mark image has invalid dimensions")
        bbox = _content_bbox(image)
        dhash64 = _dhash64(image, bbox)
    return ImageAnalysis(
        sha256=digest,
        mime_type=mime_type,
        file_extension=extension,
        byte_size=len(raw_bytes),
        width=int(width),
        height=int(height),
        content_bbox=bbox,
        dhash64=dhash64,
    )


def _asset_root(root: Path | None = None) -> Path:
    return (root or get_settings().raw_data_root).resolve()


def store_original(
    raw_bytes: bytes,
    analysis: ImageAnalysis,
    *,
    root: Path | None = None,
) -> str:
    relative = (
        Path("assets")
        / "us"
        / "mark-images"
        / analysis.sha256[:2]
        / analysis.sha256[2:4]
        / f"{analysis.sha256}{analysis.file_extension}"
    )
    path = _asset_root(root) / relative
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(raw_bytes)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return relative.as_posix()


def persist_success(
    serial_number: str,
    raw_bytes: bytes,
    *,
    source_url: str,
    source_rank: int,
    fetched_at: datetime | None = None,
    root: Path | None = None,
) -> dict[str, object]:
    ensure_mark_image_schema()
    fetched_at = fetched_at or datetime.now(timezone.utc)
    analysis = analyze_image(raw_bytes)
    storage_key = store_original(raw_bytes, analysis, root=root)
    asset_id = uuid.uuid4()
    bbox_json = json.dumps(list(analysis.content_bbox)) if analysis.content_bbox else None

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO visual.asset (
                    asset_id, sha256, mime_type, file_extension, byte_size,
                    width, height, content_bbox, dhash64, storage_key
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (sha256) DO UPDATE SET sha256 = EXCLUDED.sha256
                RETURNING asset_id, storage_key
                """,
                (
                    asset_id,
                    analysis.sha256,
                    analysis.mime_type,
                    analysis.file_extension,
                    analysis.byte_size,
                    analysis.width,
                    analysis.height,
                    bbox_json,
                    analysis.dhash64,
                    storage_key,
                ),
            )
            asset = cur.fetchone()
            canonical_asset_id = asset["asset_id"]
            canonical_storage_key = str(asset["storage_key"])

            cur.execute(
                """
                INSERT INTO visual.trademark_asset (
                    jurisdiction, serial_number, asset_id, source_url, source_rank,
                    first_observed_at, last_observed_at
                ) VALUES ('US', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (jurisdiction, serial_number) DO UPDATE SET
                    asset_id = EXCLUDED.asset_id,
                    source_url = EXCLUDED.source_url,
                    source_rank = GREATEST(
                        visual.trademark_asset.source_rank,
                        EXCLUDED.source_rank
                    ),
                    last_observed_at = EXCLUDED.last_observed_at
                """,
                (
                    serial_number,
                    canonical_asset_id,
                    source_url,
                    source_rank,
                    fetched_at,
                    fetched_at,
                ),
            )
            cur.execute(
                """
                UPDATE acquisition.us_mark_image_coverage
                SET state = 'FETCHED', asset_id = %s,
                    first_fetched_at = COALESCE(first_fetched_at, %s),
                    last_fetched_at = %s, completed_at = now(), claimed_at = NULL,
                    next_attempt_at = NULL, last_http_status = 200, last_error = NULL,
                    updated_at = now()
                WHERE serial_number = %s
                """,
                (canonical_asset_id, fetched_at, fetched_at, serial_number),
            )
            if cur.rowcount != 1:
                raise ValueError(f"unknown US mark-image coverage serial: {serial_number}")
        conn.commit()

    return {
        "serial_number": serial_number,
        "asset_id": str(canonical_asset_id),
        "sha256": analysis.sha256,
        "mime_type": analysis.mime_type,
        "byte_size": analysis.byte_size,
        "width": analysis.width,
        "height": analysis.height,
        "content_bbox": list(analysis.content_bbox) if analysis.content_bbox else None,
        "dhash64": analysis.dhash64,
        "storage_key": canonical_storage_key,
    }
