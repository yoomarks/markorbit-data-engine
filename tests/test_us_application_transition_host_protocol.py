from app.us.application_transition_host_protocol import (
    HOST_PROTOCOL_VERSION,
    build_host_summary,
    exit_code_for_report,
)


def _report(status: str, **overrides):
    report = {
        "transition_version": "CN_TO_US_APPLICATION_TRANSITION_V2",
        "status": status,
        "expected_history_parts": 91,
        "cn_checkpoint_status": "PASS",
        "cn_gate_passed": True,
        "us_pipeline_evaluated": True,
        "us_pipeline_state": "REPLAY_READY",
        "ready_for_us_application": True,
        "safe_to_start_us_replay": True,
        "reason_codes": [],
        "next_action": {"code": "RUN_REPLAY_DRY_RUN"},
        # Deliberately include nested evidence shapes that Windows PowerShell 5.1
        # does not need to deserialize for host control flow.
        "cn_checkpoint": {
            "disks": [{"name": "default", "path": "/var/lib/clickhouse/"}],
            "critical_tables": {"cn_case_current": {"active_parts": 1}},
        },
        "us_pipeline": {
            "reports": {
                "preflight": {"safe_to_replay": True},
                "replay": {"remaining": ["a.zip", "b.zip"]},
            }
        },
    }
    report.update(overrides)
    return report


def test_host_summary_is_flat_and_keeps_only_decision_fields():
    summary = build_host_summary(_report("READY_FOR_US_APPLICATION_REPLAY"))

    assert summary == {
        "host_protocol_version": HOST_PROTOCOL_VERSION,
        "transition_version": "CN_TO_US_APPLICATION_TRANSITION_V2",
        "status": "READY_FOR_US_APPLICATION_REPLAY",
        "expected_history_parts": 91,
        "cn_checkpoint_status": "PASS",
        "cn_gate_passed": True,
        "us_pipeline_evaluated": True,
        "us_pipeline_state": "REPLAY_READY",
        "ready_for_us_application": True,
        "safe_to_start_us_replay": True,
        "reason_codes": [],
        "next_action_code": "RUN_REPLAY_DRY_RUN",
    }
    assert "cn_checkpoint" not in summary
    assert "us_pipeline" not in summary
    assert all(not isinstance(value, dict) for value in summary.values())


def test_host_summary_stringifies_reason_codes_and_missing_optional_fields():
    summary = build_host_summary(
        _report(
            "US_APPLICATION_NOT_READY",
            us_pipeline_state=None,
            reason_codes=["blocked", 123],
            next_action=None,
            safe_to_start_us_replay=False,
        )
    )

    assert summary["us_pipeline_state"] == ""
    assert summary["reason_codes"] == ["blocked", "123"]
    assert summary["next_action_code"] == ""
    assert summary["safe_to_start_us_replay"] is False


def test_host_protocol_exit_codes_preserve_transition_gate_semantics():
    assert exit_code_for_report(_report("READY_FOR_US_APPLICATION_REPLAY")) == 0
    assert exit_code_for_report(_report("US_APPLICATION_ALREADY_ACCEPTED")) == 0
    assert exit_code_for_report(_report("US_APPLICATION_NOT_READY")) == 3
    assert exit_code_for_report(_report("BLOCKED_BY_CN")) == 4
