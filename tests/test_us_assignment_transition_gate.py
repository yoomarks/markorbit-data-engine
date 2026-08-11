from pathlib import Path

from app.us_assignment.transition_gate import (
    build_transition_gate,
    evaluate_transition,
)


def _application(status: str, ready: bool):
    return {
        "status": status,
        "ready_for_us_application": ready,
        "next_action": {"code": "APP_ACTION"},
    }


def _assignment(state: str, *, ready: bool = False, reasons=None):
    return {
        "state": state,
        "ready": ready,
        "reason_codes": reasons or [],
        "next_action": {"code": "ASSIGNMENT_ACTION"},
    }


def test_application_not_accepted_short_circuits_assignment_readiness():
    calls = []

    def application_builder(*_args, **_kwargs):
        return _application("READY_FOR_US_APPLICATION_REPLAY", True)

    def assignment_builder(**_kwargs):
        calls.append("assignment")
        raise AssertionError("Assignment must not be evaluated before Application acceptance")

    report = build_transition_gate(
        Path("/raw"),
        expected_history_parts=91,
        application_gate_builder=application_builder,
        assignment_readiness_builder=assignment_builder,
    )

    assert report["status"] == "BLOCKED_BY_US_APPLICATION"
    assert report["application_gate_passed"] is False
    assert report["ready_for_assignment_phase"] is False
    assert report["assignment_readiness_evaluated"] is False
    assert calls == []


def test_application_accepted_unlocks_assignment_phase():
    calls = []

    def application_builder(*_args, **_kwargs):
        return _application("US_APPLICATION_ALREADY_ACCEPTED", True)

    def assignment_builder(**kwargs):
        calls.append(kwargs)
        return _assignment(
            "SOURCE_NOT_REGISTERED",
            reasons=["no_assignment_packages_registered"],
        )

    report = build_transition_gate(
        Path("/raw"),
        expected_history_parts=91,
        verify_assignment_sources=True,
        application_gate_builder=application_builder,
        assignment_readiness_builder=assignment_builder,
    )

    assert report["status"] == "ASSIGNMENT_PHASE_UNLOCKED"
    assert report["application_gate_passed"] is True
    assert report["ready_for_assignment_phase"] is True
    assert report["assignment_ready"] is False
    assert calls == [{"raw_root": Path("/raw"), "verify_sources": True}]


def test_assignment_accepted_reports_terminal_assignment_state():
    report = evaluate_transition(
        application_gate=_application("US_APPLICATION_ALREADY_ACCEPTED", True),
        assignment_readiness=_assignment("ACCEPTED", ready=True),
        expected_history_parts=91,
    )

    assert report["status"] == "ASSIGNMENT_ACCEPTED"
    assert report["ready_for_assignment_phase"] is True
    assert report["assignment_ready"] is True


def test_assignment_data_warning_acceptance_is_still_ready():
    report = evaluate_transition(
        application_gate=_application("US_APPLICATION_ALREADY_ACCEPTED", True),
        assignment_readiness=_assignment(
            "ACCEPTED_WITH_DATA_WARNINGS",
            ready=True,
            reasons=["recorded_interest_data_warning"],
        ),
        expected_history_parts=91,
    )

    assert report["status"] == "ASSIGNMENT_ACCEPTED"
    assert report["assignment_ready"] is True
    assert report["reason_codes"] == ["recorded_interest_data_warning"]


def test_assignment_not_ready_preserves_existing_next_action():
    report = evaluate_transition(
        application_gate=_application("US_APPLICATION_ALREADY_ACCEPTED", True),
        assignment_readiness=_assignment(
            "NOT_READY",
            reasons=["assignment_ingestion_not_complete"],
        ),
        expected_history_parts=91,
    )

    assert report["status"] == "ASSIGNMENT_PHASE_UNLOCKED"
    assert report["assignment_ready"] is False
    assert report["next_action"] == {"code": "ASSIGNMENT_ACTION"}


def test_invalid_expected_history_parts_rejected_before_builders():
    try:
        build_transition_gate(
            Path("/raw"),
            expected_history_parts=0,
            application_gate_builder=lambda *_args, **_kwargs: _application(
                "US_APPLICATION_ALREADY_ACCEPTED", True
            ),
        )
    except ValueError as exc:
        assert str(exc) == "expected_history_parts must be at least 1"
    else:
        raise AssertionError("expected ValueError")
