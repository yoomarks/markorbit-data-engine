from app.cn.replay_readiness import evaluate_readiness


def _package_state(**counts):
    return {
        "status_counts": counts,
        "registered_package_count": sum(counts.values()),
        "next_pending": {"file_name": "next.zip"} if counts.get("REGISTERED") else None,
        "next_retry": {"file_name": "retry.zip"}
        if counts.get("FAILED") or counts.get("MISSING_FILE")
        else None,
        "latest_success": {"file_name": "done.zip"} if counts.get("SUCCESS") else None,
    }


def _storage_state(**overrides):
    base = {
        "missing_m16_schema": [],
        "goods_baseline_history_rows": 0,
        "reconstructible_event_baseline_rows": 0,
        "legacy_party_history_rows": 0,
        "active_stage_rows": 0,
        "storage_v2_shadow_tables": [],
        "pending_mutations": [],
        "active_bytes": 1,
        "active_rows": 1,
        "disks": [],
    }
    base.update(overrides)
    return base


def test_ready_for_normal_continuation():
    report = evaluate_readiness(
        package_state=_package_state(SUCCESS=10, REGISTERED=3),
        storage_state=_storage_state(),
        persistent_worker_running=False,
        current_engine_version="M1.6",
    )

    assert report["status"] == "READY"
    assert report["resume_mode"] == "NORMAL"
    assert report["safe_to_resume"] is True
    assert report["hard_issues"] == []
    assert report["retry_issues"] == []


def test_failed_package_requires_explicit_resume_failed():
    report = evaluate_readiness(
        package_state=_package_state(SUCCESS=10, FAILED=1, REGISTERED=2),
        storage_state=_storage_state(),
        persistent_worker_running=False,
        current_engine_version="M1.6",
    )

    assert report["status"] == "RETRY_REQUIRED"
    assert report["resume_mode"] == "RESUME_FAILED"
    assert report["safe_to_resume"] is False
    assert report["safe_to_resume_failed"] is True
    assert report["retry_issues"][0]["code"] == "FAILED_PACKAGE_RETRY_REQUIRED"


def test_storage_v2_regression_blocks_replay():
    report = evaluate_readiness(
        package_state=_package_state(SUCCESS=10, REGISTERED=2),
        storage_state=_storage_state(
            goods_baseline_history_rows=7,
            reconstructible_event_baseline_rows=9,
            legacy_party_history_rows=11,
        ),
        persistent_worker_running=False,
        current_engine_version="M1.6",
    )

    assert report["status"] == "BLOCKED"
    assert report["safe_to_resume"] is False
    assert {issue["code"] for issue in report["hard_issues"]} == {
        "STORAGE_V2_GOODS_BASELINE_REGRESSION",
        "STORAGE_V2_EVENT_BASELINE_REGRESSION",
        "STORAGE_V2_PARTY_HISTORY_REGRESSION",
    }


def test_worker_processing_shadow_mutation_and_stage_rows_block():
    report = evaluate_readiness(
        package_state=_package_state(SUCCESS=10, PROCESSING=1, REGISTERED=2),
        storage_state=_storage_state(
            active_stage_rows=123,
            storage_v2_shadow_tables=["cn_observed_event_storage_v2_shadow"],
            pending_mutations=[{"table": "cn_case_current", "mutation_id": "m1"}],
        ),
        persistent_worker_running=True,
        current_engine_version="M1.6",
    )

    codes = {issue["code"] for issue in report["hard_issues"]}
    assert report["status"] == "BLOCKED"
    assert "PERSISTENT_WORKER_RUNNING" in codes
    assert "PROCESSING_PACKAGE_PRESENT" in codes
    assert "STORAGE_V2_SHADOW_PRESENT" in codes
    assert "CLICKHOUSE_MUTATION_PENDING" in codes
    # Stage rows are not classified as orphaned while a package is PROCESSING.
    assert "ORPHAN_CN_STAGE_ROWS" not in codes


def test_orphan_stage_rows_block_when_idle():
    report = evaluate_readiness(
        package_state=_package_state(SUCCESS=10, REGISTERED=2),
        storage_state=_storage_state(active_stage_rows=123),
        persistent_worker_running=False,
        current_engine_version="M1.6",
    )

    assert report["status"] == "BLOCKED"
    assert report["hard_issues"] == [{"code": "ORPHAN_CN_STAGE_ROWS", "rows": 123}]


def test_complete_when_no_pending_or_retry_work():
    report = evaluate_readiness(
        package_state=_package_state(SUCCESS=85),
        storage_state=_storage_state(),
        persistent_worker_running=False,
        current_engine_version="M1.6",
    )

    assert report["status"] == "COMPLETE"
    assert report["resume_mode"] == "NONE"
    assert report["safe_to_resume"] is False
