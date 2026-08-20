from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

from app.db import postgres_conn
from app.us_mark_image.migrations import ensure_mark_image_schema
from app.us_mark_image.planner import MarkImageCandidate, _upsert_candidates
from app.us_mark_image.processor import analyze_image, persist_success


def _png_bytes(*, margin: int = 20) -> bytes:
    image = Image.new("RGB", (180, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((margin, 25, 180 - margin, 75), outline="black", width=4)
    draw.line((margin + 12, 50, 180 - margin - 12, 50), fill="black", width=4)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _reset() -> None:
    ensure_mark_image_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE visual.trademark_asset, acquisition.us_mark_image_coverage, visual.asset")
            cur.execute(
                """
                UPDATE acquisition.us_mark_image_planner_state
                SET backfill_serial_cursor = '', request_not_before = NULL, updated_at = now()
                WHERE state_key = 'US_MARK_IMAGE'
                """
            )
        conn.commit()


def main() -> None:
    _reset()
    filing = date(2026, 8, 20)
    standard = MarkImageCandidate("90100001", 101, filing, "ALPHA", "4", True)
    design_a = MarkImageCandidate("90100002", 102, filing, "ALPHA DESIGN", "3", False)
    design_b = MarkImageCandidate("90100003", 103, filing, "ALPHA DESIGN", "3", False)

    seeded = _upsert_candidates(
        [standard, design_a, design_b],
        priority=1_000_000,
        reason_code="RECENT_APPLICATION",
    )
    assert seeded == {"observed": 3, "queued": 2, "not_applicable": 1}, seeded

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT serial_number, state FROM acquisition.us_mark_image_coverage ORDER BY serial_number"
            )
            states = {row["serial_number"]: row["state"] for row in cur.fetchall()}
    assert states == {
        "90100001": "NOT_APPLICABLE",
        "90100002": "QUEUED",
        "90100003": "QUEUED",
    }, states

    raw = _png_bytes()
    analysis = analyze_image(raw)
    assert analysis.mime_type == "image/png", analysis
    assert analysis.width == 180 and analysis.height == 100, analysis
    assert analysis.content_bbox is not None, analysis
    assert len(analysis.dhash64) == 16, analysis

    fetched_at = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        first = persist_success(
            design_a.serial_number,
            raw,
            source_url=design_a.source_url,
            source_rank=design_a.source_rank,
            fetched_at=fetched_at,
            root=root,
        )
        second = persist_success(
            design_b.serial_number,
            raw,
            source_url=design_b.source_url,
            source_rank=design_b.source_rank,
            fetched_at=fetched_at,
            root=root,
        )
        assert first["asset_id"] == second["asset_id"], (first, second)
        assert first["storage_key"] == second["storage_key"], (first, second)
        stored = list((root / "assets" / "us" / "mark-images").rglob("*.png"))
        assert len(stored) == 1, stored

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM visual.asset")
            assert int(cur.fetchone()["n"]) == 1
            cur.execute("SELECT count(*) AS n FROM visual.trademark_asset")
            assert int(cur.fetchone()["n"]) == 2
            cur.execute(
                "SELECT serial_number, state FROM acquisition.us_mark_image_coverage ORDER BY serial_number"
            )
            final_states = {row["serial_number"]: row["state"] for row in cur.fetchall()}
    assert final_states["90100001"] == "NOT_APPLICABLE", final_states
    assert final_states["90100002"] == "FETCHED", final_states
    assert final_states["90100003"] == "FETCHED", final_states

    # Re-observing the same official mark fingerprint must not waste a fetch.
    _upsert_candidates([design_a], priority=100_000, reason_code="HISTORICAL_BACKFILL")
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state FROM acquisition.us_mark_image_coverage WHERE serial_number = %s",
                (design_a.serial_number,),
            )
            assert cur.fetchone()["state"] == "FETCHED"

    # A real mark-fingerprint change reopens exactly that case for image acquisition.
    changed = MarkImageCandidate("90100002", 202, filing, "ALPHA DESIGN UPDATED", "3", False)
    _upsert_candidates([changed], priority=1_000_000, reason_code="RECENT_APPLICATION")
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state, attempts FROM acquisition.us_mark_image_coverage WHERE serial_number = %s",
                (changed.serial_number,),
            )
            reopened = cur.fetchone()
    assert reopened["state"] == "QUEUED", reopened
    assert int(reopened["attempts"]) == 0, reopened

    print(
        json.dumps(
            {
                "status": "PASS",
                "standard_character": "NOT_APPLICABLE",
                "exact_duplicate_assets": 1,
                "trademark_asset_links": 2,
                "fingerprint_change_requeued": True,
            }
        )
    )


if __name__ == "__main__":
    main()
