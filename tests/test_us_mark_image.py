from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

from app.us_mark_image.planner import MarkImageCandidate
from app.us_mark_image.processor import analyze_image, store_original


def _image_bytes(*, left: int = 20, right: int = 160) -> bytes:
    image = Image.new("RGB", (180, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((left, 30, right, 70), outline="black", width=4)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_candidate_uses_official_image_url_and_mark_fingerprint() -> None:
    candidate = MarkImageCandidate(
        "90817045",
        42,
        date(2026, 8, 20),
        "TENTREND",
        "3",
        False,
    )
    assert candidate.source_url == "https://tsdr.uspto.gov/img/90817045/large"
    assert len(candidate.source_mark_fingerprint) == 64


def test_image_analysis_keeps_original_metadata_and_extracts_visual_fingerprint() -> None:
    raw = _image_bytes()
    analysis = analyze_image(raw)
    assert analysis.mime_type == "image/png"
    assert analysis.file_extension == ".png"
    assert analysis.width == 180
    assert analysis.height == 100
    assert analysis.byte_size == len(raw)
    assert analysis.content_bbox is not None
    assert len(analysis.sha256) == 64
    assert len(analysis.dhash64) == 16


def test_content_addressed_store_writes_exact_bytes_once() -> None:
    raw = _image_bytes()
    analysis = analyze_image(raw)
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        first = store_original(raw, analysis, root=root)
        second = store_original(raw, analysis, root=root)
        assert first == second
        path = root / first
        assert path.read_bytes() == raw
        assert len(list((root / "assets" / "us" / "mark-images").rglob("*.png"))) == 1
