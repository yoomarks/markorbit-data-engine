from app.cn.final_checkpoint import build_final_checkpoint, evaluate_final_checkpoint


def _readiness(status: str, **storage_overrides):
    storage = {
        "active_bytes": 100,
        "active_rows": 200,
        "goods_baseline_history_rows": 0,
        "reconstructible_event_baseline_rows": 0,
        "legacy_party_history_rows": 0,
        "active_stage_rows": 0,
        "storage_v2_shadow_tables": [],
        "pending_mutations": [],
    }
    storage.update(storage_overrides)
    return {
        "status": status,
        "hard_issues": [],
        "retry_issues": [],
        "packages": {
            "registered_package_count": 85,
            "status_counts": {"SUCCESS": 85}
            if status == "COMPLETE"
            else {"SUCCESS": 84, "REGISTERED": 1},
        },
        "storage_v2": storage,
    }


def test_checkpoint_short_circuits_heavy_acceptance_until_complete():
    calls = []

    def readiness_builder(**_kwargs):
        return _readiness("READY")

    def acceptance_builder():
        calls.append("acceptance")
        raise AssertionError("acceptance must not run before replay is complete")

    report = build_final_checkpoint(
        readiness_builder=readiness_builder,
        acceptance_builder=acceptance_builder,
    )

    assert report["status"] == "NOT_READY"
    assert report["ready_for_next_domain"] is False
    assert report["acceptance_executed"] is False
    assert calls == []


def test_retry_required_is_blocked_without_acceptance_scan():
    readiness = _readiness("RETRY_REQUIRED")
    readiness["retry_issues"] = [{"code": "FAILED_PACKAGE_RETRY_REQUIRED"}]

    report = evaluate_final_checkpoint(readiness=readiness, acceptance=None)

    assert report["status"] == "BLOCKED"
    assert report["reasons"] == [{"code": "FAILED_PACKAGE_RETRY_REQUIRED"}]
    assert report["acceptance_executed"] is False


def test_complete_plus_pass_is_ready_for_next_domain():
    acceptance = {
        "status": "PASS",
        "hard_fail_reasons": [],
        "not_ready_reasons": [],
        "warning_reasons": [],
    }

    report = evaluate_final_checkpoint(
        readiness=_readiness("COMPLETE"), acceptance=acceptance
    )

    assert report["status"] == "PASS"
    assert report["ready_for_next_domain"] is True
    assert report["summary"]["registered_package_count"] == 85
    assert report["summary"]["package_status_counts"] == {"SUCCESS": 85}
    assert report["summary"]["active_stage_rows"] == 0


def test_complete_plus_pass_with_warnings_remains_accepted():
    acceptance = {
        "status": "PASS_WITH_WARNINGS",
        "hard_fail_reasons": [],
        "not_ready_reasons": [],
        "warning_reasons": ["source_backed_incomplete_official_party_records"],
    }

    report = evaluate_final_checkpoint(
        readiness=_readiness("COMPLETE"), acceptance=acceptance
    )

    assert report["status"] == "PASS_WITH_WARNINGS"
    assert report["ready_for_next_domain"] is True
    assert report["summary"]["acceptance_warning_reasons"] == [
        "source_backed_incomplete_official_party_records"
    ]


def test_complete_plus_acceptance_failure_blocks_next_domain():
    acceptance = {
        "status": "FAIL",
        "hard_fail_reasons": ["duplicates_after_final"],
        "not_ready_reasons": [],
        "warning_reasons": [],
    }

    report = evaluate_final_checkpoint(
        readiness=_readiness("COMPLETE"), acceptance=acceptance
    )

    assert report["status"] == "FAIL"
    assert report["ready_for_next_domain"] is False
    assert report["reasons"] == [
        {"code": "duplicates_after_final", "source": "acceptance_hard_fail"}
    ]


def test_build_checkpoint_runs_acceptance_once_when_complete():
    calls = []

    def readiness_builder(**_kwargs):
        return _readiness("COMPLETE")

    def acceptance_builder():
        calls.append("acceptance")
        return {
            "status": "PASS",
            "hard_fail_reasons": [],
            "not_ready_reasons": [],
            "warning_reasons": [],
        }

    report = build_final_checkpoint(
        readiness_builder=readiness_builder,
        acceptance_builder=acceptance_builder,
    )

    assert report["status"] == "PASS"
    assert report["acceptance_executed"] is True
    assert calls == ["acceptance"]
