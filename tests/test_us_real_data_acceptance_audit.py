from __future__ import annotations

from copy import deepcopy

from app.us.audit_real_data import (
    ALL_TABLE_KEYS,
    AUDIT_VERSION,
    DAILY_RANK_MAJOR,
    US_SCHEMA_VERSION,
    evaluate_acceptance,
)


def _profile(*, tombstones: int = 0, version: str = US_SCHEMA_VERSION) -> dict:
    return {
        "totals": {
            "schema_version": version,
            "row_counts": {"markorbit_facts.us_case_current": 10},
            "snapshot_tombstone_counts": {
                "markorbit_facts.us_owner_current": tombstones,
            },
        }
    }


def _packages() -> list[dict]:
    return [
        {
            "package_id": "11111111-1111-1111-1111-111111111111",
            "file_name": "apc18840407-20251231-05.zip",
            "sha256": "1" * 64,
            "package_kind": "HISTORICAL_APPLICATIONS",
            "partition_value": "1884-04-07/2025-12-31#005",
            "source_period_start": "1884-04-07",
            "source_period_end": "2025-12-31",
            "source_rank": 1_020_251_231_005_001,
            "status": "SUCCESS",
            "profile": _profile(),
            "error_message": None,
        },
        {
            "package_id": "22222222-2222-2222-2222-222222222222",
            "file_name": "apc260108.zip",
            "sha256": "2" * 64,
            "package_kind": "DAILY_APPLICATIONS",
            "partition_value": "2026-01-08",
            "source_period_start": "2026-01-08",
            "source_period_end": "2026-01-08",
            "source_rank": DAILY_RANK_MAJOR + 20_260_108 * 1_000_000 + 2,
            "status": "SUCCESS",
            "profile": _profile(tombstones=2),
            "error_message": None,
        },
    ]


def _table_metrics() -> dict[str, dict[str, int]]:
    return {
        table: {
            "row_count": 10,
            "unique_keys": 10,
            "serial_count": 8,
            "duplicate_keys_after_final": 0,
        }
        for table in ALL_TABLE_KEYS
    }


def _lineage(packages: list[dict]) -> dict[str, list[dict]]:
    daily = packages[1]
    return {
        table: [
            {
                "package_id": daily["package_id"],
                "min_source_rank": daily["source_rank"],
                "max_source_rank": daily["source_rank"],
                "row_count": 10,
            }
        ]
        for table in ALL_TABLE_KEYS
    }


def _evaluate(
    *,
    packages: list[dict] | None = None,
    table_metrics: dict[str, dict[str, int]] | None = None,
    orphan_counts: dict[str, int] | None = None,
    lineage_metrics: dict[str, list[dict]] | None = None,
    verification: dict | None = None,
    postgres_version: str = US_SCHEMA_VERSION,
    clickhouse_versions: list[str] | None = None,
) -> dict:
    packages = deepcopy(packages if packages is not None else _packages())
    return evaluate_acceptance(
        packages=packages,
        postgres_schema_version=postgres_version,
        clickhouse_schema_versions=clickhouse_versions or ["US_M1.0", "US_M1.1", "US_M1.2", US_SCHEMA_VERSION],
        table_metrics=deepcopy(table_metrics if table_metrics is not None else _table_metrics()),
        orphan_counts=deepcopy(orphan_counts if orphan_counts is not None else {}),
        lineage_metrics=deepcopy(lineage_metrics if lineage_metrics is not None else _lineage(packages)),
        source_kind_case_counts={"HISTORICAL_APPLICATIONS": 100, "DAILY_APPLICATIONS": 8},
        source_file_verification=deepcopy(verification),
    )


def test_complete_replay_without_sha_verification_passes_with_warning() -> None:
    result = _evaluate()
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert result["audit_version"] == AUDIT_VERSION
    assert result["hard_fail_reasons"] == []
    assert result["not_ready_reasons"] == []
    assert result["warning_reasons"] == ["source_sha_verification_not_requested"]
    assert result["coverage"]["rank_boundary_ok"] is True
    assert result["coverage"]["historical_end"] == "2025-12-31"
    assert result["coverage"]["daily_end"] == "2026-01-08"
    assert result["snapshot_reconciliation"]["total_tombstones"] == 2


def test_complete_replay_with_verified_sources_passes() -> None:
    result = _evaluate(
        verification={
            "requested": True,
            "checked_count": 2,
            "missing_count": 0,
            "mismatch_count": 0,
            "missing": [],
            "mismatched": [],
        }
    )
    assert result["status"] == "PASS"
    assert result["warning_reasons"] == []


def test_pending_or_old_profile_is_not_ready_not_corruption() -> None:
    packages = _packages()
    packages[0]["profile"] = _profile(version="US_M1.2")
    packages.append(
        {
            "package_id": "33333333-3333-3333-3333-333333333333",
            "file_name": "apc260109.zip",
            "sha256": "3" * 64,
            "package_kind": "DAILY_APPLICATIONS",
            "partition_value": "2026-01-09",
            "source_period_start": "2026-01-09",
            "source_period_end": "2026-01-09",
            "source_rank": DAILY_RANK_MAJOR + 20_260_109 * 1_000_000 + 3,
            "status": "REGISTERED",
            "profile": {},
            "error_message": None,
        }
    )
    result = _evaluate(packages=packages, lineage_metrics=_lineage(packages))
    assert result["status"] == "NOT_READY"
    assert "registered_replay_not_complete" in result["not_ready_reasons"]
    assert "successful_packages_require_m13_replay" in result["not_ready_reasons"]
    assert result["hard_fail_reasons"] == []


def test_integrity_violations_fail_closed() -> None:
    packages = _packages()
    packages[1]["status"] = "FAILED"
    packages[1]["error_message"] = "parser failed"
    metrics = _table_metrics()
    metrics["us_owner_current"]["duplicate_keys_after_final"] = 1
    lineage = _lineage(packages)
    lineage["us_case_current"][0]["max_source_rank"] += 99
    result = _evaluate(
        packages=packages,
        table_metrics=metrics,
        orphan_counts={"us_statement_current": 1},
        lineage_metrics=lineage,
        verification={
            "requested": True,
            "checked_count": 1,
            "missing_count": 1,
            "mismatch_count": 1,
            "missing": [{"file_name": "missing.zip"}],
            "mismatched": [{"file_name": "bad.zip"}],
        },
    )
    assert result["status"] == "FAIL"
    for reason in (
        "failed_or_missing_source_packages",
        "duplicates_after_final",
        "subordinate_rows_without_case",
        "source_lineage_rank_mismatch",
        "authoritative_source_files_missing",
        "authoritative_source_sha_mismatch",
    ):
        assert reason in result["hard_fail_reasons"]


def test_history_daily_replay_requires_populated_m13_fact_tables() -> None:
    metrics = _table_metrics()
    metrics["us_madrid_filing_current"]["row_count"] = 0
    result = _evaluate(table_metrics=metrics)
    assert result["status"] == "FAIL"
    assert "m13_fact_tables_empty_after_history_daily_replay" in result["hard_fail_reasons"]
    assert "us_madrid_filing_current" in result["integrity"]["empty_tables"]


def test_precedence_violation_fails_even_if_ingestion_status_is_success() -> None:
    packages = _packages()
    packages[0]["source_rank"] = packages[1]["source_rank"] + 1
    result = _evaluate(packages=packages, lineage_metrics=_lineage(packages))
    assert result["status"] == "FAIL"
    assert "source_rank_precedence_violation" in result["hard_fail_reasons"]


def test_no_registered_us_packages_is_not_ready() -> None:
    result = evaluate_acceptance(
        packages=[],
        postgres_schema_version=US_SCHEMA_VERSION,
        clickhouse_schema_versions=[US_SCHEMA_VERSION],
        table_metrics={
            table: {
                "row_count": 0,
                "unique_keys": 0,
                "serial_count": 0,
                "duplicate_keys_after_final": 0,
            }
            for table in ALL_TABLE_KEYS
        },
        orphan_counts={},
        lineage_metrics={table: [] for table in ALL_TABLE_KEYS},
        source_kind_case_counts={},
        source_file_verification=None,
    )
    assert result["status"] == "NOT_READY"
    assert "no_us_packages_registered" in result["not_ready_reasons"]
    assert "historical_baseline_not_successful" in result["not_ready_reasons"]
    assert "daily_update_not_successful" in result["not_ready_reasons"]
