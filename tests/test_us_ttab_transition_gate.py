from pathlib import Path

from app.us_ttab.transition_gate import build_transition_gate, evaluate_transition


def _assignment(status: str, ready: bool):
    return {
        "status": status,
        "assignment_ready": ready,
        "next_action": {"code": "ASSIGNMENT_ACTION"},
    }


def _ttab(state: str, *, ready: bool = False, reasons=None):
    return {
        "state": state,
        "ready": ready,
        "reason_codes": reasons or [],
        "next_action": {"code": "TTAB_ACTION"},
    }


def test_assignment_not_accepted_short_circuits_ttab_readiness():
    calls = []

    def assignment_builder(*_args, **_kwargs):
        return _assignment("ASSIGNMENT_PHASE_UNLOCKED", False)

    def ttab_builder(**_kwargs):
        calls.append("ttab")
        raise AssertionError("TTAB must not be evaluated before Assignment acceptance")

    report = build_transition_gate(
        Path("/raw"),
        expected_history_parts=91,
        assignment_gate_builder=assignment_builder,
        ttab_readiness_builder=ttab_builder,
    )

    assert report["status"] == "BLOCKED_BY_US_ASSIGNMENT"
    assert report["assignment_gate_passed"] is False
    assert report["ready_for_ttab_phase"] is False
    assert report["ttab_readiness_evaluated"] is False
    assert calls == []


def test_assignment_accepted_unlocks_ttab_phase():
    calls = []

    def assignment_builder(*_args, **_kwargs):
        return _assignment("ASSIGNMENT_ACCEPTED", True)

    def ttab_builder(**kwargs):
        calls.append(kwargs)
        return _ttab("SOURCE_NOT_REGISTERED", reasons=["no_ttab_packages_registered"])

    report = build_transition_gate(
        Path("/raw"),
        expected_history_parts=91,
        verify_ttab_sources=True,
        assignment_gate_builder=assignment_builder,
        ttab_readiness_builder=ttab_builder,
    )

    assert report["status"] == "TTAB_PHASE_UNLOCKED"
    assert report["assignment_gate_passed"] is True
    assert report["ready_for_ttab_phase"] is True
    assert report["ttab_ready"] is False
    assert calls == [{"raw_root": Path("/raw"), "verify_sources": True}]


def test_ttab_accepted_reports_terminal_state():
    report = evaluate_transition(
        assignment_gate=_assignment("ASSIGNMENT_ACCEPTED", True),
        ttab_readiness=_ttab("ACCEPTED", ready=True),
        expected_history_parts=91,
    )

    assert report["status"] == "TTAB_ACCEPTED"
    assert report["ttab_ready"] is True
    assert report["ready_for_ttab_phase"] is True


def test_ttab_data_warning_acceptance_is_still_ready():
    report = evaluate_transition(
        assignment_gate=_assignment("ASSIGNMENT_ACCEPTED", True),
        ttab_readiness=_ttab(
            "ACCEPTED_WITH_DATA_WARNINGS",
            ready=True,
            reasons=["ttab_source_data_warning"],
        ),
        expected_history_parts=91,
    )

    assert report["status"] == "TTAB_ACCEPTED"
    assert report["ttab_ready"] is True
    assert report["reason_codes"] == ["ttab_source_data_warning"]


def test_ttab_not_ready_preserves_existing_next_action():
    report = evaluate_transition(
        assignment_gate=_assignment("ASSIGNMENT_ACCEPTED", True),
        ttab_readiness=_ttab("NOT_READY", reasons=["ttab_ingestion_not_complete"]),
        expected_history_parts=91,
    )

    assert report["status"] == "TTAB_PHASE_UNLOCKED"
    assert report["ttab_ready"] is False
    assert report["next_action"] == {"code": "TTAB_ACTION"}


def test_invalid_expected_history_parts_rejected_before_builders():
    try:
        build_transition_gate(
            Path("/raw"),
            expected_history_parts=0,
            assignment_gate_builder=lambda *_args, **_kwargs: _assignment(
                "ASSIGNMENT_ACCEPTED", True
            ),
        )
    except ValueError as exc:
        assert str(exc) == "expected_history_parts must be at least 1"
    else:
        raise AssertionError("expected ValueError")
