from datetime import date
from pathlib import Path

from app.us.sample_audit import audit_application_sample


REAL_TDXF = Path("tests/fixtures/us_real_tdxf_layout.xml")
DAILY = Path("tests/fixtures/us_m1_daily.xml")


def test_application_sample_audit_profiles_real_tdxf() -> None:
    report = audit_application_sample(
        REAL_TDXF,
        source_kind="HISTORICAL",
        effective_date=date(2025, 12, 31),
    )
    assert report["status"] in {"PASS", "PASS_WITH_WARNINGS"}
    assert report["counts"]["cases"] > 0
    assert report["source"]["effective_date_inferred_from_filename"] is False
    assert report["gate"]["parser_completed"] is True
    assert report["gate"]["scale_up_authorized"] is False


def test_application_sample_audit_keeps_source_metadata_explicit() -> None:
    report = audit_application_sample(DAILY, source_kind="DAILY")
    assert report["source"]["source_kind"] == "DAILY"
    assert report["source"]["effective_date"] is None
    assert report["source"]["effective_date_inferred_from_filename"] is False
    assert report["source"]["persistent_xml_extraction"] is False


def test_application_sample_audit_rejects_missing_file(tmp_path: Path) -> None:
    report = audit_application_sample(tmp_path / "missing.zip", source_kind="DAILY")
    assert report["status"] == "FAIL"
    assert report["gate"]["ready_for_sample_ingest"] is False
