from pathlib import Path

from app.admin_api import _job_domain, _raw_class, _raw_domain


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
