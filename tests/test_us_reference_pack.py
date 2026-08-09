from datetime import date
import json
from pathlib import Path

import pytest

from app.us.reference_evidence import sha256_file
from app.us.reference_pack import build_reference_pack


USPTO_URL = "https://www.uspto.gov/trademarks/trademark-updates-and-announcements/xml-resources"


def _csv(path: Path, rows: str) -> None:
    path.write_text(
        "code,official_description,official_definition,official_category,source_locator\n"
        + rows,
        encoding="utf-8",
    )


def test_status_pack_binds_source_and_reviewed_csv_hashes(tmp_path: Path) -> None:
    source = tmp_path / "Table1TrademarkStatusCodes_20250813.doc"
    source.write_bytes(b"fixture-official-doc-bytes")
    reviewed = tmp_path / "reviewed.csv"
    _csv(reviewed, "700,Fixture status,,,Table 1 row 700\n")

    report = build_reference_pack(
        family="status",
        source_document=source,
        reviewed_csv=reviewed,
        reference_version="USPTO_STATUS_CODES_TEST_20250813",
        document_date=date(2025, 8, 13),
        source_url=USPTO_URL,
    )
    payload_path = Path(report["payload_path"])
    manifest_path = Path(report["manifest_path"])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["records"][0]["code"] == "700"
    assert payload["source"]["sha256"] == sha256_file(source)
    assert manifest["source_document_sha256"] == sha256_file(source)
    assert manifest["reviewed_transcription_sha256"] == sha256_file(reviewed)
    assert manifest["normalized_payload_sha256"] == payload[
        "normalized_payload_sha256"
    ]
    assert report["source_evidence"]["source_document_sha256"] == sha256_file(source)


def test_event_pack_uses_existing_event_normalizer(tmp_path: Path) -> None:
    source = tmp_path / "TrademarkApplicationsDocumentation.doc"
    source.write_bytes(b"event-doc")
    reviewed = tmp_path / "events.csv"
    _csv(reviewed, "newap,Fixture new application,,,event row\n")
    report = build_reference_pack(
        family="event",
        source_document=source,
        reviewed_csv=reviewed,
        reference_version="USPTO_EVENT_CODES_TEST_20250813",
        document_date=date(2025, 8, 13),
        source_url=USPTO_URL,
    )
    payload = json.loads(Path(report["payload_path"]).read_text(encoding="utf-8"))
    assert payload["records"][0]["code"] == "NEWAP"


def test_pack_requires_payload_beside_source_document(tmp_path: Path) -> None:
    source = tmp_path / "source.doc"
    source.write_bytes(b"source")
    reviewed = tmp_path / "reviewed.csv"
    _csv(reviewed, "700,Fixture status,,,row\n")
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(ValueError, match="beside the official source document"):
        build_reference_pack(
            family="status",
            source_document=source,
            reviewed_csv=reviewed,
            reference_version="USPTO_STATUS_TEST",
            document_date=date(2025, 8, 13),
            source_url=USPTO_URL,
            output_path=other / "payload.json",
        )


def test_pack_requires_reviewed_csv_in_same_evidence_directory(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    csv_dir = tmp_path / "review"
    source_dir.mkdir()
    csv_dir.mkdir()
    source = source_dir / "source.doc"
    source.write_bytes(b"source")
    reviewed = csv_dir / "reviewed.csv"
    _csv(reviewed, "700,Fixture status,,,row\n")
    with pytest.raises(ValueError, match="self-contained"):
        build_reference_pack(
            family="status",
            source_document=source,
            reviewed_csv=reviewed,
            reference_version="USPTO_STATUS_TEST",
            document_date=date(2025, 8, 13),
            source_url=USPTO_URL,
        )


def test_pack_rejects_unreviewable_csv_shape(tmp_path: Path) -> None:
    source = tmp_path / "source.doc"
    source.write_bytes(b"source")
    reviewed = tmp_path / "reviewed.csv"
    reviewed.write_text("code,notes\n700,no description\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        build_reference_pack(
            family="status",
            source_document=source,
            reviewed_csv=reviewed,
            reference_version="USPTO_STATUS_TEST",
            document_date=date(2025, 8, 13),
            source_url=USPTO_URL,
        )
