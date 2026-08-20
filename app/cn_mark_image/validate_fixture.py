from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw

from app.cn_mark_image.importer import import_zip_package
from app.cn_mark_image.migrations import ensure_cn_mark_image_schema
from app.db import postgres_conn


def _jpeg_bytes(*, left: int, size: tuple[int, int] = (600, 400)) -> bytes:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((left, 80, size[0] - 40, size[1] - 80), outline="black", width=8)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _zip(path: Path, members: dict[str, bytes]) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _scalar(cur, sql: str) -> int:
    cur.execute(sql)
    row = cur.fetchone()
    return int(next(iter(row.values())))


def main() -> None:
    ensure_cn_mark_image_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                TRUNCATE TABLE
                    visual.cn_trademark_visual_current,
                    visual.cn_trademark_visual_version,
                    visual.cn_mark_image_observation,
                    visual.asset_derivative,
                    visual.canonical_asset,
                    acquisition.cn_mark_image_package,
                    acquisition.us_mark_image_coverage,
                    visual.trademark_asset,
                    visual.asset
                CASCADE
                """
            )
        conn.commit()

    image_a = _jpeg_bytes(left=40)
    image_b = _jpeg_bytes(left=180)

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        raw_root = root / "raw"
        processed_root = root / "processed"
        package_one = root / "historical-001.zip"
        package_two = root / "update-001.zip"
        package_three = root / "update-002.zip"

        _zip(
            package_one,
            {
                "logos/12345678.jpg": image_a,
                "logos/not-mapped-name.jpg": image_a,
                "README.txt": b"transport metadata",
            },
        )
        first = import_zip_package(
            package_one,
            package_kind="HISTORICAL",
            source_rank=1,
            raw_root=raw_root,
            processed_root=processed_root,
            commit_interval=1,
        )
        assert first.state == "ACCEPTED"
        assert first.jpeg_entry_count == 2
        assert first.processed_jpeg_count == 2
        assert first.mapped_application_count == 1
        assert first.unmapped_subject_count == 1
        assert first.new_raw_asset_count == 1
        assert first.reused_raw_asset_count == 1
        assert first.unique_raw_asset_count == 1
        assert first.unique_canonical_asset_count == 1

        repeated = import_zip_package(
            package_one,
            package_kind="HISTORICAL",
            source_rank=1,
            raw_root=raw_root,
            processed_root=processed_root,
            commit_interval=1,
        )
        assert repeated.package_id == first.package_id
        assert repeated.processed_jpeg_count == first.processed_jpeg_count

        _zip(
            package_two,
            {
                "logos/12345678.jpg": image_a,
                "logos/87654321.jpg": image_b,
            },
        )
        second = import_zip_package(
            package_two,
            package_kind="UPDATE",
            source_rank=2,
            raw_root=raw_root,
            processed_root=processed_root,
            delete_source_on_acceptance=True,
            commit_interval=1,
        )
        assert second.state == "ACCEPTED"
        assert second.source_deleted is True
        assert not package_two.exists()
        assert second.reused_raw_asset_count == 1
        assert second.new_raw_asset_count == 1

        _zip(package_three, {"logos/12345678.jpg": image_b})
        third = import_zip_package(
            package_three,
            package_kind="UPDATE",
            source_rank=3,
            raw_root=raw_root,
            processed_root=processed_root,
            commit_interval=1,
        )
        assert third.state == "ACCEPTED"
        assert third.reused_raw_asset_count == 1

        raw_files = list((raw_root / "assets" / "raw" / "cn" / "mark-images").rglob("*.jpg"))
        processed_files = list(
            (processed_root / "assets" / "processed" / "cn" / "mark-images").rglob("*.jpg")
        )
        assert len(raw_files) == 2
        assert len(processed_files) == 2

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            assert _scalar(cur, "SELECT count(*) FROM visual.asset") == 2
            assert _scalar(cur, "SELECT count(*) FROM visual.canonical_asset") == 2
            assert _scalar(cur, "SELECT count(*) FROM visual.cn_mark_image_observation") == 5
            assert _scalar(cur, "SELECT count(*) FROM visual.cn_trademark_visual_current") == 2
            assert _scalar(cur, "SELECT count(*) FROM visual.cn_trademark_visual_version") == 3
            cur.execute(
                """
                SELECT v.raw_asset_id = c.raw_asset_id AS current_is_latest
                FROM visual.cn_trademark_visual_current c
                JOIN visual.cn_trademark_visual_version v
                  ON v.application_number = c.application_number
                 AND v.raw_asset_id = c.raw_asset_id
                WHERE c.application_number = '12345678'
                  AND c.source_rank = 3
                """
            )
            row = cur.fetchone()
            assert row and row["current_is_latest"] is True

    print("CN mark-image bulk fixture PASS")


if __name__ == "__main__":
    main()
