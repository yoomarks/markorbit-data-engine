from pathlib import Path

from app.four_domain_acceptance import _archive_summary, evaluate_four_domain_acceptance


def _bundle() -> dict:
    return {
        "policy": {
            "expected_application_history_parts": 91,
            "expected_application_daily_through": "2026-08-09",
        },
        "reports": {
            "cn": {
                "audit": "CN_M16_ACCEPTANCE_INTEGRITY",
                "status": "PASS",
                "hard_fail_reasons": [],
                "not_ready_reasons": [],
                "warning_reasons": [],
                "package_registry": {"all_success": True, "non_success_count": 0},
            },
            "application": {
                "audit_version": "US_M14_REAL_DATA_ACCEPTANCE_V2_HISTORY_PARTS",
                "status": "PASS_WITH_WARNINGS",
                "hard_fail_reasons": [],
                "not_ready_reasons": [],
                "warning_reasons": ["source_sha_verification_not_requested"],
                "historical_part_completeness": {
                    "complete": True,
                    "expected_history_parts": 91,
                },
                "coverage": {"daily_end": "2026-08-09"},
            },
            "assignment": {
                "audit": "US_ASSIGNMENT_MANIFEST_ACCEPTANCE_V1",
                "status": "PASS",
                "hard_fail_reasons": [],
                "not_ready_reasons": [],
                "warning_reasons": [],
                "manifest_registry": {
                    "missing_registry_sha256": [],
                    "extra_registry_sha256": [],
                    "incomplete_sha256": [],
                    "metadata_mismatches": [],
                },
                "legal_ownership_conclusion": False,
            },
            "ttab": {
                "audit": "US_TTAB_MANIFEST_ACCEPTANCE_V1",
                "status": "PASS_WITH_WARNINGS",
                "hard_fail_reasons": [],
                "not_ready_reasons": [],
                "warning_reasons": ["some_or_all_ttab_snapshots_have_no_docket_rows"],
                "manifest_registry": {
                    "missing_registry_sha256": [],
                    "extra_registry_sha256": [],
                    "incomplete_sha256": [],
                    "metadata_mismatches": [],
                },
                "deadline_validity_inference": False,
                "legal_outcome_conclusion": False,
                "substantive_rights_conclusion": False,
            },
        },
    }


def _archive_evidence() -> dict:
    clean = {
        "all_registered_success": True,
        "missing_archive_count": 0,
        "outside_raw_archive_count": 0,
    }
    return {domain: dict(clean) for domain in ("cn", "application", "assignment", "ttab")}


def test_four_domain_acceptance_allows_only_documented_lightweight_warnings() -> None:
    result = evaluate_four_domain_acceptance(_bundle(), _archive_evidence())
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert result["hard_fail_reasons"] == []
    assert "application:source_sha_verification_not_requested" in result["warning_reasons"]
    assert result["semantics"]["cross_domain_legal_event_order_inference"] is False


def test_four_domain_acceptance_rejects_unknown_ttab_warning() -> None:
    bundle = _bundle()
    bundle["reports"]["ttab"]["warning_reasons"].append("guessed_deadline_validity")
    result = evaluate_four_domain_acceptance(bundle, _archive_evidence())
    assert result["status"] == "FAIL"
    assert "ttab:warnings_outside_allowed_coverage_set" in result["hard_fail_reasons"]


def test_four_domain_acceptance_requires_assignment_formal_pass() -> None:
    bundle = _bundle()
    bundle["reports"]["assignment"]["status"] = "PASS_WITH_WARNINGS"
    bundle["reports"]["assignment"]["warning_reasons"] = ["malformed_serials"]
    result = evaluate_four_domain_acceptance(bundle, _archive_evidence())
    assert result["status"] == "FAIL"
    assert "assignment:status_PASS_WITH_WARNINGS_not_formal_pass" in result["hard_fail_reasons"]


def test_four_domain_acceptance_requires_pinned_application_daily_end() -> None:
    bundle = _bundle()
    bundle["reports"]["application"]["coverage"]["daily_end"] = "2026-08-08"
    result = evaluate_four_domain_acceptance(bundle, _archive_evidence())
    assert result["status"] == "FAIL"
    assert "application:daily_coverage_end_does_not_match_pinned_policy" in result["hard_fail_reasons"]


def test_four_domain_acceptance_requires_physical_archives() -> None:
    evidence = _archive_evidence()
    evidence["cn"]["missing_archive_count"] = 1
    result = evaluate_four_domain_acceptance(_bundle(), evidence)
    assert result["status"] == "FAIL"
    assert "cn:successful_source_archive_missing" in result["hard_fail_reasons"]


def test_archive_summary_requires_successful_sources_under_raw_archive(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    good = archive_root / "good.zip"
    good.write_bytes(b"good")
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"outside")
    summary = _archive_summary(
        [
            {"file_name": "good.zip", "status": "SUCCESS", "archived_path": str(good)},
            {"file_name": "missing.zip", "status": "SUCCESS", "archived_path": ""},
            {"file_name": "outside.zip", "status": "SUCCESS", "archived_path": str(outside)},
        ],
        archive_root=archive_root,
    )
    assert summary["missing_archive_count"] == 1
    assert summary["outside_raw_archive_count"] == 1
    assert summary["all_registered_success"] is True
