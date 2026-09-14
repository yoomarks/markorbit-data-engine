from pathlib import Path

from app.cn.serving_state_checkpoint import CHECKPOINT_VERSION as CN_SERVING_CHECKPOINT_VERSION
from app.us.application_transition_gate import (
    build_transition_gate,
    evaluate_transition,
)


def _cn(status: str, ready: bool):
    return {
        "status": status,
        "ready_for_next_domain": ready,
        "reasons": [],
    }


def _cn_serving(status: str = "PASS", **overrides):
    report = {
        "checkpoint_version": CN_SERVING_CHECKPOINT_VERSION,
        "status": status,
        "read_only": True,
        "evidence_mode": "LIGHTWEIGHT_SERVING_CHECKPOINT",
        "expected_package_success": True,
        "quiescent": True,
        "core_tables_ready": True,
        "goods_schema_exact": True,
        "full_corpus_scan": False,
        "package_reprocessed": False,
        "reasons": [],
    }
    report.update(overrides)
    return report


def _us(state: str, *, ready: bool = False, reasons=None):
    return {
        "state": state,
        "ready": ready,
        "reason_codes": reasons or [],
        "next_action": {"code": "TEST_ACTION"},
    }


def test_cn_not_ready_short_circuits_us_pipeline():
    calls = []

    def cn_builder(**_kwargs):
        return _cn("NOT_READY", False)

    def us_builder(*_args, **_kwargs):
        calls.append("us")
        raise AssertionError("US pipeline must not be evaluated before CN acceptance")

    report = build_transition_gate(
        Path("/raw"),
        expected_history_parts=91,
        cn_checkpoint_builder=cn_builder,
        us_readiness_builder=us_builder,
    )

    assert report["status"] == "BLOCKED_BY_CN"
    assert report["cn_gate_passed"] is False
    assert report["us_pipeline_evaluated"] is False
    assert report["safe_to_start_us_replay"] is False
    assert calls == []


def test_cn_failure_short_circuits_us_pipeline():
    report = evaluate_transition(
        cn_checkpoint=_cn("FAIL", False),
        us_pipeline=None,
        expected_history_parts=91,
    )

    assert report["status"] == "BLOCKED_BY_CN"
    assert report["ready_for_us_application"] is False


def test_legacy_cn_pass_with_warnings_remains_accepted_for_transition():
    calls = []

    def cn_builder(**_kwargs):
        return _cn("PASS_WITH_WARNINGS", True)

    def us_builder(raw_root, **kwargs):
        calls.append((raw_root, kwargs))
        return _us("REPLAY_READY")

    report = build_transition_gate(
        Path("/raw"),
        expected_history_parts=91,
        deep_source_test=True,
        cn_checkpoint_builder=cn_builder,
        us_readiness_builder=us_builder,
    )

    assert report["status"] == "READY_FOR_US_APPLICATION_REPLAY"
    assert report["cn_gate_passed"] is True
    assert report["ready_for_us_application"] is True
    assert report["safe_to_start_us_replay"] is True
    assert calls[0][0] == Path("/raw")
    assert calls[0][1]["expected_history_parts"] == 91
    assert calls[0][1]["deep_source_test"] is True


def test_lightweight_cn_pass_preserves_us_91_part_readiness_evaluation():
    calls = []

    def cn_builder():
        return _cn_serving("PASS")

    def us_builder(raw_root, **kwargs):
        calls.append((raw_root, kwargs))
        return _us("REPLAY_READY")

    report = build_transition_gate(
        Path("/raw"),
        expected_history_parts=91,
        verify_source_files=True,
        cn_checkpoint_builder=cn_builder,
        us_readiness_builder=us_builder,
    )

    assert report["status"] == "READY_FOR_US_APPLICATION_REPLAY"
    assert report["cn_gate_passed"] is True
    assert report["safe_to_start_us_replay"] is True
    assert calls == [
        (
            Path("/raw"),
            {
                "expected_history_parts": 91,
                "deep_source_test": False,
                "verify_source_files": True,
            },
        )
    ]


def test_lightweight_cn_warn_is_accepted_without_full_corpus_reaudit():
    report = evaluate_transition(
        cn_checkpoint=_cn_serving("WARN"),
        us_pipeline=_us("REPLAY_READY"),
        expected_history_parts=91,
    )

    assert report["status"] == "READY_FOR_US_APPLICATION_REPLAY"
    assert report["cn_gate_passed"] is True


def test_lightweight_cn_missing_required_serving_invariant_fails_closed():
    calls = []

    def us_builder(*_args, **_kwargs):
        calls.append("us")
        return _us("REPLAY_READY")

    report = build_transition_gate(
        Path("/raw"),
        expected_history_parts=91,
        cn_checkpoint_builder=lambda: _cn_serving(
            "PASS", expected_package_success=False
        ),
        us_readiness_builder=us_builder,
    )

    assert report["status"] == "BLOCKED_BY_CN"
    assert report["cn_gate_passed"] is False
    assert report["reason_codes"] == ["cn_serving_checkpoint_not_accepted"]
    assert calls == []


def test_persistent_worker_blocks_before_cn_or_us_builders():
    calls = []

    def cn_builder():
        calls.append("cn")
        return _cn_serving("PASS")

    def us_builder(*_args, **_kwargs):
        calls.append("us")
        return _us("REPLAY_READY")

    report = build_transition_gate(
        Path("/raw"),
        expected_history_parts=91,
        persistent_worker_running=True,
        cn_checkpoint_builder=cn_builder,
        us_readiness_builder=us_builder,
    )

    assert report["status"] == "BLOCKED_BY_CN"
    assert report["safe_to_start_us_replay"] is False
    assert calls == []


def test_cn_pass_but_us_source_blocked_is_not_ready():
    report = evaluate_transition(
        cn_checkpoint=_cn("PASS", True),
        us_pipeline=_us(
            "SOURCE_CORPUS_BLOCKED",
            reasons=["historical_part_sequence_incomplete"],
        ),
        expected_history_parts=91,
    )

    assert report["status"] == "US_APPLICATION_NOT_READY"
    assert report["cn_gate_passed"] is True
    assert report["safe_to_start_us_replay"] is False
    assert report["reason_codes"] == ["historical_part_sequence_incomplete"]


def test_replay_ready_is_only_state_that_allows_replay_start():
    report = evaluate_transition(
        cn_checkpoint=_cn("PASS", True),
        us_pipeline=_us("REPLAY_READY"),
        expected_history_parts=91,
    )

    assert report["status"] == "READY_FOR_US_APPLICATION_REPLAY"
    assert report["safe_to_start_us_replay"] is True


def test_us_already_accepted_does_not_request_replay():
    report = evaluate_transition(
        cn_checkpoint=_cn("PASS", True),
        us_pipeline=_us("ACCEPTED", ready=True),
        expected_history_parts=91,
    )

    assert report["status"] == "US_APPLICATION_ALREADY_ACCEPTED"
    assert report["ready_for_us_application"] is True
    assert report["safe_to_start_us_replay"] is False


def test_invalid_expected_history_parts_rejected_before_builders():
    try:
        build_transition_gate(
            Path("/raw"),
            expected_history_parts=0,
            cn_checkpoint_builder=lambda: _cn("PASS", True),
        )
    except ValueError as exc:
        assert str(exc) == "expected_history_parts must be at least 1"
    else:
        raise AssertionError("expected ValueError")


def _target_epoch(checkpoint: int) -> dict:
    return {
        "run_id": "bulk-run",
        "plan_sha256": "a" * 64,
        "checkpoint_sequence": checkpoint,
        "final_audit_version": "US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V2",
    }


def _touch_application_sources(raw_root: Path, count: int) -> None:
    archive = raw_root / "archive" / "us"
    archive.mkdir(parents=True, exist_ok=True)
    for index in range(1, count + 1):
        (archive / f"p{index:03d}.zip").write_bytes(b"")


def test_stale_target_bulk_epoch_does_not_bypass_current_source_corpus(tmp_path):
    _touch_application_sources(tmp_path, 343)
    calls = []

    def us_builder(*_args, **_kwargs):
        calls.append("legacy")
        return _us("STAGING_REQUIRED", reasons=["pending_source_requires_archive_staging"])

    report = build_transition_gate(
        tmp_path,
        expected_history_parts=91,
        cn_checkpoint_builder=lambda: _cn_serving("PASS"),
        us_readiness_builder=us_builder,
        target_bulk_epoch_builder=lambda: _target_epoch(310),
    )

    assert report["status"] == "US_APPLICATION_NOT_READY"
    assert calls == ["legacy"]


def test_matching_target_bulk_epoch_short_circuits_legacy_replay_planner(tmp_path):
    _touch_application_sources(tmp_path, 343)

    def legacy_builder(*_args, **_kwargs):
        raise AssertionError("matching accepted target-bulk epoch must bypass legacy replay planner")

    report = build_transition_gate(
        tmp_path,
        expected_history_parts=91,
        cn_checkpoint_builder=lambda: _cn_serving("PASS"),
        us_readiness_builder=legacy_builder,
        target_bulk_epoch_builder=lambda: _target_epoch(343),
    )

    assert report["status"] == "US_APPLICATION_ALREADY_ACCEPTED"
    assert report["us_pipeline"]["evidence_mode"] == "TARGET_BULK_ACCEPTED_EPOCH"
    assert report["us_pipeline"]["accepted_target_sequence_count"] == 343


def test_target_bulk_epoch_source_verification_remains_fail_closed(tmp_path):
    _touch_application_sources(tmp_path, 343)

    report = build_transition_gate(
        tmp_path,
        expected_history_parts=91,
        verify_source_files=True,
        cn_checkpoint_builder=lambda: _cn_serving("PASS"),
        us_readiness_builder=lambda *_args, **_kwargs: _us("STAGING_REQUIRED"),
        target_bulk_epoch_builder=lambda: _target_epoch(343),
        source_preflight_builder=lambda *_args, **_kwargs: {
            "safe_to_replay": False,
            "hard_issue_types": ["SOURCE_SHA_DRIFT"],
            "not_ready_reasons": [],
            "source_inventory": {"semantic_source_count": 343},
        },
    )

    assert report["status"] == "US_APPLICATION_NOT_READY"
    assert report["reason_codes"] == ["SOURCE_SHA_DRIFT"]
