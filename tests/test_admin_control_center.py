from pathlib import Path
from types import SimpleNamespace

import pytest

from app.admin_api import _job_domain, _raw_class, _raw_domain, _raw_inventory


def test_raw_inventory_classifies_all_primary_domains_without_source_semantics():
    assert _raw_domain(Path("incoming/cn/2023_4.zip")) == "CN"
    assert _raw_domain(Path("archive/us/apc260809.zip")) == "US_APPLICATION"
    assert _raw_domain(Path("incoming/us_assignment/daily/assignment.xml")) == "US_ASSIGNMENT"
    assert _raw_domain(Path("archive/us_ttab/historical/ttab.zip")) == "US_TTAB"

    assert _raw_class("CN", Path("incoming/cn/2023_4.zip")) == "CN_MONTHLY"
    assert (
        _raw_class(
            "US_APPLICATION",
            Path("incoming/us/apc18840407-20251231-91.zip"),
        )
        == "APPLICATION_HISTORICAL"
    )
    assert (
        _raw_class("US_APPLICATION", Path("archive/us/apc260809.zip"))
        == "APPLICATION_DAILY"
    )
    assert (
        _raw_class(
            "US_ASSIGNMENT",
            Path("incoming/us_assignment/daily/assignment.xml"),
        )
        == "ASSIGNMENT_DAILY"
    )
    assert (
        _raw_class(
            "US_TTAB",
            Path("archive/us_ttab/historical/ttab.zip"),
        )
        == "TTAB_HISTORICAL"
    )


def test_job_domain_classification_keeps_us_subdomains_separate():
    assert _job_domain("CN_PACKAGE_INGESTION") == "CN"
    assert _job_domain("US_PACKAGE_INGESTION") == "US_APPLICATION"
    assert _job_domain("US_ASSIGNMENT_PACKAGE_INGESTION") == "US_ASSIGNMENT"
    assert _job_domain("US_TTAB_PACKAGE_INGESTION") == "US_TTAB"


def test_raw_inventory_filters_and_pages_without_materializing_full_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_root = tmp_path / "raw"
    cn = raw_root / "incoming" / "cn"
    us = raw_root / "archive" / "us"
    cn.mkdir(parents=True)
    us.mkdir(parents=True)
    for name in ("2023_1.zip", "2023_2.zip", "2023_3.zip"):
        (cn / name).write_bytes(name.encode())
    (us / "apc260809.zip").write_bytes(b"us")
    monkeypatch.setattr(
        "app.admin_api.get_settings", lambda: SimpleNamespace(raw_data_root=raw_root)
    )

    result = _raw_inventory(
        limit=2,
        offset=1,
        domain="CN",
        area_filter="incoming",
        query="2023",
        scan_limit=10,
    )

    assert result["total_files"] == 4
    assert result["matched_files"] == 3
    assert result["files_returned"] == 2
    assert all(item["domain"] == "CN" for item in result["files"])


def test_raw_inventory_fails_closed_at_scan_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_root = tmp_path / "raw"
    incoming = raw_root / "incoming" / "cn"
    incoming.mkdir(parents=True)
    for index in range(3):
        (incoming / f"2023_{index}.zip").write_bytes(b"x")
    monkeypatch.setattr(
        "app.admin_api.get_settings", lambda: SimpleNamespace(raw_data_root=raw_root)
    )

    with pytest.raises(RuntimeError, match="bounded limit of 2 files"):
        _raw_inventory(limit=1, scan_limit=2)


def test_admin_routes_and_control_center_markup_are_present():
    import app.main as main

    routes = {route.path for route in main.app.routes}
    assert "/api/admin/overview" in routes
    assert "/api/admin/raw-inventory" in routes
    assert "/api/admin/packages" in routes
    assert "/api/admin/packages/{package_id}" in routes
    assert "/api/admin/jobs" in routes

    markup = Path("web/index.html").read_text(encoding="utf-8")
    assert "US Application" in markup
    assert "Assignment" in markup
    assert "TTAB" in markup
    assert "Raw 数据" in markup
    assert "来源包明细" in markup
    assert "任务进展" in markup
    assert "/api/admin/overview" in markup
