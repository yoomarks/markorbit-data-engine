from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

from app.cn_mark_image.importer import infer_application_number
from app.cn_mark_image.storage import (
    analyze_jpeg,
    canonicalize_jpeg,
    content_addressed_key,
    store_content_addressed,
)


def _jpeg_bytes(*, size: tuple[int, int] = (300, 200), quality: int = 90) -> bytes:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, size[0] - 20, size[1] - 20), outline="black", width=5)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def test_cn_jpeg_analysis_separates_raw_and_decoded_pixel_identity() -> None:
    raw = _jpeg_bytes()
    same_pixels_different_file = raw + b"\x00\x00"
    first = analyze_jpeg(raw)
    second = analyze_jpeg(same_pixels_different_file)
    assert first.raw_sha256 != second.raw_sha256
    assert first.pixel_sha256 == second.pixel_sha256
    assert first.width == 300
    assert first.height == 200
    assert len(first.dhash64) == 16


def test_small_cn_jpeg_is_not_needlessly_reencoded() -> None:
    raw = _jpeg_bytes()
    canonical = canonicalize_jpeg(raw)
    assert canonical.data == raw
    assert canonical.transformed is False


def test_large_cn_jpeg_is_downsampled_without_upscaling_small_sources() -> None:
    raw = _jpeg_bytes(size=(3000, 3000), quality=95)
    canonical = canonicalize_jpeg(raw, max_edge=1600)
    assert max(canonical.width, canonical.height) == 1600
    assert canonical.transformed is True
    assert canonical.byte_size < len(raw)


def test_cn_content_addressed_key_never_exposes_application_number() -> None:
    raw = _jpeg_bytes()
    digest = analyze_jpeg(raw).raw_sha256
    key = content_addressed_key(tier="raw", sha256=digest)
    assert digest in key.as_posix()
    assert "12345678" not in key.as_posix()
    assert key.as_posix().startswith("assets/raw/cn/mark-images/")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        first = store_content_addressed(raw, root=root, relative_key=key)
        second = store_content_addressed(raw, root=root, relative_key=key)
        assert first == second
        assert (root / key).read_bytes() == raw


def test_cn_application_number_inference_fails_closed() -> None:
    assert infer_application_number("12345678") == "12345678"
    assert infer_application_number("12345678_1") is None
    assert infer_application_number("logo-12345678") is None
