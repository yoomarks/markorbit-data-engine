from pathlib import Path

from app.us_ttab.audit_real_data import evaluate_acceptance
from app.us_ttab.readiness import evaluate_readiness


def _package(status: str = "SUCCESS") -> dict[str, object]:
    return {
        "package_id": "00000000-0000-0000-0000-000000000001",
        "status": status,
        "schema_version": "US_TTAB_M1.1",
    }


def _tables() -> dict[str, dict[str, int]]:
    return {
        table: {
            "row_count": 1,
            "unique_observation_keys": 1,
            "proceeding_count": 1,
            "source_package_count": 1,
            "duplicate_observation_keys": 0,
        }
        for table in (
            "us_ttab_proceeding_history",
            "us_ttab_party_history",
            "us_ttab_property_history",
            "us_ttab_docket_history",
        )
    }


def _lineage() -> dict[str, dict[str, int]]:
    return {
        table: {
            "missing_registry_package_rows": 0,
            "wrong_jurisdiction_rows": 0,
            "source_rank_mismatch_rows": 0,
        }
        for table in _tables()
    }


def _projection() -> dict[str, int]:
    return {
        "latest_projection_count": 1,
        "latest_proceeding_count": 1,
        "latest_source_package_count": 1,
        "latest_property_count": 1,
        "property_serial_count": 1,
        "malformed_property_serial_count": 0,
        "property_serial_joined_to_us_case_count": 1,
        "latest_docket_count": 2,
        "due_date_observation_count": 1,
    }


def _acceptance(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "PASS",
        "hard_fail_reasons": [],
        "not_ready_reasons": [],
        "warning_reasons": [],
    }
    result.update(overrides)
    return result


def test_ttab_acceptance_passes_source_backed_integrity() -> None:
    result = evaluate_acceptance(
        packages=[_package()], schema={"ready": True}, tables=_tables(),
        orphans={"us_ttab_party_history": 0, "us_ttab_property_history": 0, "us_ttab_docket_history": 0},
        lineage=_lineage(), projection=_projection(),
        source_verification={"missing_count": 0, "mismatch_count": 0}, verify_sources=True,
    )
    assert result["status"] == "PASS"
    assert result["hard_fail_reasons"] == []
    assert result["warning_reasons"] == []


def test_ttab_acceptance_keeps_coverage_gaps_as_warnings() -> None:
    projection = _projection()
    projection["malformed_property_serial_count"] = 1
    projection["property_serial_joined_to_us_case_count"] = 0
    result = evaluate_acceptance(
        packages=[_package()], schema={"ready": True}, tables=_tables(),
        orphans={"us_ttab_party_history": 0, "us_ttab_property_history": 0, "us_ttab_docket_history": 0},
        lineage=_lineage(), projection=projection,
        source_verification={"missing_count": 0, "mismatch_count": 0}, verify_sources=True,
    )
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert "malformed_ttab_property_serials_present" in result["warning_reasons"]
    assert "some_ttab_property_serials_not_present_in_us_case_current" in result["warning_reasons"]


def test_ttab_acceptance_fails_duplicate_or_orphan_history() -> None:
    tables = _tables()
    tables["us_ttab_docket_history"]["duplicate_observation_keys"] = 1
    result = evaluate_acceptance(
        packages=[_package()], schema={"ready": True}, tables=tables,
        orphans={"us_ttab_party_history": 0, "us_ttab_property_history": 1, "us_ttab_docket_history": 0},
        lineage=_lineage(), projection=_projection(),
        source_verification={"missing_count": 0, "mismatch_count": 0}, verify_sources=True,
    )
    assert result["status"] == "FAIL"
    assert "duplicate_observation_keys:us_ttab_docket_history" in result["hard_fail_reasons"]
    assert "orphan_snapshot_children:us_ttab_property_history" in result["hard_fail_reasons"]


def test_ttab_readiness_requires_source_verification_when_only_warning() -> None:
    result = evaluate_readiness(
        packages=[_package()],
        acceptance=_acceptance(status="PASS_WITH_WARNINGS", warning_reasons=["ttab_source_sha_verification_not_requested"]),
        verify_sources=False,
    )
    assert result["state"] == "SOURCE_VERIFICATION_REQUIRED"
    assert result["ready"] is False
    assert result["deadline_validity_inference"] is False
    assert result["legal_outcome_conclusion"] is False


def test_ttab_readiness_accepts_verified_corpus() -> None:
    result = evaluate_readiness(packages=[_package()], acceptance=_acceptance(), verify_sources=True)
    assert result["state"] == "ACCEPTED"
    assert result["ready"] is True
    assert result["substantive_rights_conclusion"] is False


def test_ttab_parser_does_not_use_generic_nested_type_or_status() -> None:
    source = Path("app/us_ttab/parser.py").read_text(encoding="utf-8")
    proceeding_section = source.split("def parse_proceeding", 1)[1]
    assert '"case-type", "type"' not in proceeding_section
    assert '"status-text", "status"' not in proceeding_section
