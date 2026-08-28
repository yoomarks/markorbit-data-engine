from app.us.capacity_pilot import APPLICATION_OUTPUT_TABLES, build_pilot_receipt
from app.us.ingest import OUTPUT_PACKAGE_COLUMNS


def _profile(**tables: tuple[int, int]) -> dict[str, object]:
    return {
        "profile_version": "DATA_ENGINE_STORAGE_CAPACITY_PROFILE_V1",
        "read_only": True,
        "tables": [
            {"table": table, "bytes_on_disk": values[0], "rows": values[1]}
            for table, values in tables.items()
        ],
    }


def _dry_run() -> dict[str, object]:
    return {
        "status": "READY",
        "safe_to_execute": True,
        "next_step": {
            "sequence": 1,
            "file_name": "historical_01.zip",
            "path": "/data/raw/incoming/us/historical_01.zip",
            "sha256": "a" * 64,
        },
        "preflight": {
            "source_inventory": {
                "sources": [
                    {
                        "file_name": "historical_01.zip",
                        "path": "/data/raw/incoming/us/historical_01.zip",
                        "file_size": 1_000,
                        "sha256": "a" * 64,
                        "package_kind": "HISTORICAL_APPLICATIONS",
                        "partition_value": "1",
                    }
                ]
            }
        },
    }


def _replay() -> dict[str, object]:
    return {
        "mode": "APPLY",
        "status": "PAUSED",
        "processed_count": 1,
        "processed": [
            {
                "sequence": 1,
                "file_name": "historical_01.zip",
                "sha256": "a" * 64,
                "metrics": {"case_count": 10},
            }
        ],
    }


def test_capacity_pilot_output_tables_match_ingest_cleanup_contract() -> None:
    assert APPLICATION_OUTPUT_TABLES == {
        qualified.split(".", 1)[1] for qualified in OUTPUT_PACKAGE_COLUMNS
    }


def test_one_package_receipt_measures_hot_table_family_deltas() -> None:
    before = _profile(us_case_current=(100, 10), us_owner_current=(50, 10))
    after = _profile(
        us_case_current=(160, 20),
        us_owner_current=(90, 25),
        us_event_history=(20, 5),
    )
    result = build_pilot_receipt(
        engine_sha="b" * 40,
        dry_run=_dry_run(),
        replay=_replay(),
        before_profile=before,
        after_profile=after,
    )
    assert result["status"] == "PASS"
    assert result["projection_input_ready"] is True
    assert result["pilot"]["raw_bytes"] == 1_000
    assert result["pilot"]["warm_bytes"] == 0
    assert result["pilot"]["hot_bytes"] == 120
    assert result["pilot"]["rows"] == 30
    assert result["pilot"]["hot_bytes_by_table_family"] == {
        "case_core": 60,
        "event_history": 20,
        "party_contact": 40,
    }
    assert result["pilot"]["receipt_identity"].startswith("us-capacity-pilot:")


def test_receipt_blocks_if_any_unrelated_us_table_changes() -> None:
    result = build_pilot_receipt(
        engine_sha="b" * 40,
        dry_run=_dry_run(),
        replay=_replay(),
        before_profile=_profile(us_case_current=(100, 10), us_assignment_current=(20, 2)),
        after_profile=_profile(us_case_current=(150, 20), us_assignment_current=(21, 3)),
    )
    assert result["status"] == "BLOCKED"
    assert any(
        issue["type"] == "CONCURRENT_OR_UNEXPECTED_US_TABLE_CHANGE"
        for issue in result["issues"]
    )


def test_receipt_blocks_unstable_negative_active_part_delta() -> None:
    result = build_pilot_receipt(
        engine_sha="b" * 40,
        dry_run=_dry_run(),
        replay=_replay(),
        before_profile=_profile(us_case_current=(100, 10)),
        after_profile=_profile(us_case_current=(90, 20)),
    )
    assert result["status"] == "BLOCKED"
    assert any(
        issue["type"] == "ACTIVE_PART_DELTA_NOT_STABLE_FOR_MEASUREMENT"
        for issue in result["issues"]
    )


def test_receipt_requires_exactly_one_applied_package() -> None:
    replay = _replay()
    replay["processed_count"] = 2
    result = build_pilot_receipt(
        engine_sha="b" * 40,
        dry_run=_dry_run(),
        replay=replay,
        before_profile=_profile(us_case_current=(100, 10)),
        after_profile=_profile(us_case_current=(150, 20)),
    )
    assert result["status"] == "BLOCKED"
    assert any(issue["type"] == "EXACTLY_ONE_PACKAGE_REQUIRED" for issue in result["issues"])


def test_receipt_binds_processed_package_to_dry_run_next_step() -> None:
    replay = _replay()
    replay["processed"][0]["sha256"] = "c" * 64
    result = build_pilot_receipt(
        engine_sha="b" * 40,
        dry_run=_dry_run(),
        replay=replay,
        before_profile=_profile(us_case_current=(100, 10)),
        after_profile=_profile(us_case_current=(150, 20)),
    )
    assert result["status"] == "BLOCKED"
    assert result["issues"] == [{"type": "PROCESSED_PACKAGE_DOES_NOT_MATCH_DRY_RUN_NEXT_STEP"}]


def test_receipt_requires_positive_measured_hot_and_rows() -> None:
    result = build_pilot_receipt(
        engine_sha="b" * 40,
        dry_run=_dry_run(),
        replay=_replay(),
        before_profile=_profile(us_case_current=(100, 10)),
        after_profile=_profile(us_case_current=(100, 10)),
    )
    assert result["status"] == "BLOCKED"
    types = {issue["type"] for issue in result["issues"]}
    assert "NO_POSITIVE_HOT_BYTE_DELTA" in types
    assert "NO_POSITIVE_ROW_DELTA" in types
