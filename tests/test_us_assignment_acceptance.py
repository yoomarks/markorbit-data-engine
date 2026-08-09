from pathlib import Path

from app.us_assignment.audit_real_data import evaluate_acceptance
from app.us_assignment.readiness import evaluate_readiness
from app.us_assignment.reconciliation import classify_name_evidence


def _package(status: str = "SUCCESS") -> dict:
    return {
        "package_id": "11111111-1111-1111-1111-111111111111",
        "status": status,
        "source_rank": 123,
        "profile": {"schema_version": "US_ASSIGNMENT_M1.0"},
    }


def _tables() -> dict:
    return {
        table: {
            "row_count": 1,
            "unique_observation_keys": 1,
            "reel_frame_count": 1,
            "source_package_count": 1,
            "duplicate_observation_keys": 0,
        }
        for table in (
            "us_assignment_record_history",
            "us_assignment_assignor_history",
            "us_assignment_assignee_history",
            "us_assignment_property_history",
        )
    }


def _lineage() -> dict:
    return {
        table: [
            {
                "package_id": "11111111-1111-1111-1111-111111111111",
                "min_source_rank": 123,
                "max_source_rank": 123,
                "row_count": 1,
            }
        ]
        for table in _tables()
    }


def _projection(**overrides) -> dict:
    value = {
        "latest_record_count": 1,
        "latest_reel_frame_count": 1,
        "latest_source_package_count": 1,
        "latest_property_count": 1,
        "malformed_serial_count": 0,
        "property_serial_count": 1,
        "property_serial_joined_to_case_count": 1,
    }
    value.update(overrides)
    return value


def _acceptance(*, verification=None, projection=None, packages=None) -> dict:
    return evaluate_acceptance(
        packages=packages or [_package()],
        schema={"ready": True},
        tables=_tables(),
        lineage=_lineage(),
        orphans={},
        projection=projection or _projection(),
        source_verification=verification,
    )


def test_assignment_acceptance_requires_source_verification_for_clean_pass() -> None:
    result = _acceptance()
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert result["warning_reasons"] == ["assignment_source_sha_verification_not_requested"]
    assert result["legal_ownership_conclusion"] is False


def test_assignment_acceptance_passes_with_verified_sources() -> None:
    result = _acceptance(
        verification={"missing_count": 0, "mismatch_count": 0, "checked_count": 1}
    )
    assert result["status"] == "PASS"
    assert result["hard_fail_reasons"] == []


def test_assignment_malformed_serial_is_warning_not_corruption() -> None:
    result = _acceptance(
        verification={"missing_count": 0, "mismatch_count": 0},
        projection=_projection(malformed_serial_count=2),
    )
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert "malformed_assignment_property_serials_present" in result["warning_reasons"]


def test_assignment_lineage_mismatch_fails_closed() -> None:
    lineage = _lineage()
    lineage["us_assignment_record_history"][0]["max_source_rank"] = 999
    result = evaluate_acceptance(
        packages=[_package()],
        schema={"ready": True},
        tables=_tables(),
        lineage=lineage,
        orphans={},
        projection=_projection(),
        source_verification={"missing_count": 0, "mismatch_count": 0},
    )
    assert result["status"] == "FAIL"
    assert "assignment_source_lineage_rank_mismatch" in result["hard_fail_reasons"]


def test_readiness_routes_unverified_clean_acceptance_to_source_verification() -> None:
    acceptance = _acceptance()
    result = evaluate_readiness(
        packages=[_package()], acceptance=acceptance, verify_sources=False
    )
    assert result["state"] == "SOURCE_VERIFICATION_REQUIRED"
    assert result["ready"] is False


def test_readiness_accepts_verified_corpus() -> None:
    acceptance = _acceptance(
        verification={"missing_count": 0, "mismatch_count": 0}
    )
    result = evaluate_readiness(
        packages=[_package()], acceptance=acceptance, verify_sources=True
    )
    assert result["state"] == "ACCEPTED"
    assert result["ready"] is True
    assert result["legal_ownership_conclusion"] is False


def test_reconciliation_classification_is_name_evidence_only() -> None:
    assert classify_name_evidence(
        case_exists=True,
        current_owner_names=["Beta Brand Inc."],
        recorded_assignee_names=[" beta   brand inc. "],
    ) == "NAME_SET_MATCH"
    assert classify_name_evidence(
        case_exists=True,
        current_owner_names=["Owner LLC"],
        recorded_assignee_names=["Other Inc."],
    ) == "NAME_SET_DIFFER"
    assert classify_name_evidence(
        case_exists=False,
        current_owner_names=[],
        recorded_assignee_names=["Other Inc."],
    ) == "RECORDED_ASSIGNMENT_WITHOUT_CASE_RECORD"


def test_assignment_acceptance_and_reconciliation_routes_are_get_only() -> None:
    import app.main as main

    expected = {
        "/api/us/assignments/acceptance",
        "/api/us/assignments/readiness",
        "/api/us/assignments/reconciliation",
    }
    paths = {route.path for route in main.app.routes}
    assert expected.issubset(paths)
    for route in main.app.routes:
        if route.path in expected:
            assert route.methods == {"GET"}


def test_assignment_reporting_scripts_exist() -> None:
    for name in (
        "audit-us-assignment-real-data.ps1",
        "check-us-assignment-readiness.ps1",
        "export-us-assignment-reconciliation.ps1",
    ):
        assert Path("scripts", name).is_file()
