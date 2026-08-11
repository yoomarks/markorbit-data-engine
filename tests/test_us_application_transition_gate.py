from pathlib import Path

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


def test_cn_pass_with_warnings_is_accepted_for_transition():
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
            cn_checkpoint_builder=lambda **_kwargs: _cn("PASS", True),
        )
    except ValueError as exc:
        assert str(exc) == "expected_history_parts must be at least 1"
    else:
        raise AssertionError("expected ValueError")
