from pathlib import Path

from app.us_ttab import TTAB_SCHEMA_VERSION


def test_ttab_m11_rawxml_contract_remains_regressed_under_m12() -> None:
    assert TTAB_SCHEMA_VERSION == "US_TTAB_M1.2"
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Probe real TTABVUE raw XML contract" not in ci
    assert "probe_real_rawxml" not in ci
    assert "US TTAB M1.1 real-rawxml contract fixture" in ci


def test_ttab_m11_offline_real_layout_covers_four_public_shapes() -> None:
    for name in (
        "us_ttab_real_opposition.xml",
        "us_ttab_real_cancellation.xml",
        "us_ttab_real_exparte.xml",
        "us_ttab_real_extension.xml",
    ):
        assert Path("tests/fixtures", name).is_file()


def test_ttabvue_capture_is_explicit_not_background_ingestion() -> None:
    source = Path("app/us_ttab/capture_ttabvue.py").read_text(encoding="utf-8")
    script = Path("scripts/capture-us-ttabvue.ps1").read_text(encoding="utf-8")
    assert "rawxml" in source
    assert "register_ttab_source" in source
    assert "ingest_ttab_package" not in source
    assert "--snapshot-at" in source
    assert "capture-us-ttabvue" not in Path(".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )
    assert "app.us_ttab.capture_ttabvue" in script


def test_temporary_online_probe_file_is_removed_before_merge() -> None:
    assert not Path("app/us_ttab/probe_real_rawxml.py").exists()
