from pathlib import Path

from app.domain_lifecycle import build_lifecycle, evaluate_lifecycle


def _cn(status: str, accepted: bool):
    return {"status": status, "ready_for_next_domain": accepted}


def _application(status: str, *, cn_status="PASS", cn_accepted=True, next_code="APP_ACTION"):
    return {
        "status": status,
        "ready_for_us_application": status
        in {"READY_FOR_US_APPLICATION_REPLAY", "US_APPLICATION_ALREADY_ACCEPTED"},
        "cn_checkpoint": _cn(cn_status, cn_accepted),
        "next_action": {"code": next_code},
    }


def _assignment(status: str, *, application=None, ready=False, next_code="ASSIGNMENT_ACTION"):
    return {
        "status": status,
        "assignment_ready": ready,
        "application_gate": application
        or _application("US_APPLICATION_ALREADY_ACCEPTED"),
        "next_action": {"code": next_code},
    }


def _ttab(status: str, *, assignment=None, ready=False, next_code="TTAB_ACTION"):
    return {
        "status": status,
        "ttab_ready": ready,
        "assignment_gate": assignment
        or _assignment("ASSIGNMENT_ACCEPTED", ready=True),
        "next_action": {"code": next_code},
    }


def test_cn_incomplete_reports_cn_phase():
    application = _application(
        "BLOCKED_BY_CN",
        cn_status="NOT_READY",
        cn_accepted=False,
        next_code="COMPLETE_CN_AND_PASS_FINAL_CHECKPOINT",
    )
    report = evaluate_lifecycle(
        _ttab(
            "BLOCKED_BY_US_ASSIGNMENT",
            assignment=_assignment(
                "BLOCKED_BY_US_APPLICATION",
                application=application,
                ready=False,
            ),
        )
    )

    assert report["current_phase"] == "CN"
    assert report["status"] == "IN_PROGRESS"
    assert report["gates"]["cn"]["accepted"] is False
    assert report["next_action"]["code"] == "COMPLETE_CN_AND_PASS_FINAL_CHECKPOINT"


def test_application_replay_ready_reports_application_phase():
    application = _application("READY_FOR_US_APPLICATION_REPLAY")
    report = evaluate_lifecycle(
        _ttab(
            "BLOCKED_BY_US_ASSIGNMENT",
            assignment=_assignment(
                "BLOCKED_BY_US_APPLICATION",
                application=application,
                ready=False,
            ),
        )
    )

    assert report["current_phase"] == "US_APPLICATION"
    assert report["status"] == "IN_PROGRESS"
    assert report["gates"]["cn"]["accepted"] is True
    assert report["gates"]["us_application"]["accepted"] is False


def test_assignment_unlocked_reports_assignment_phase():
    report = evaluate_lifecycle(
        _ttab(
            "BLOCKED_BY_US_ASSIGNMENT",
            assignment=_assignment("ASSIGNMENT_PHASE_UNLOCKED", ready=False),
        )
    )

    assert report["current_phase"] == "US_ASSIGNMENT"
    assert report["status"] == "IN_PROGRESS"
    assert report["gates"]["us_application"]["accepted"] is True
    assert report["gates"]["us_assignment"]["accepted"] is False
    assert report["next_action"]["code"] == "ASSIGNMENT_ACTION"


def test_ttab_unlocked_reports_ttab_phase():
    report = evaluate_lifecycle(_ttab("TTAB_PHASE_UNLOCKED", ready=False))

    assert report["current_phase"] == "US_TTAB"
    assert report["status"] == "IN_PROGRESS"
    assert report["gates"]["us_assignment"]["accepted"] is True
    assert report["gates"]["us_ttab"]["accepted"] is False
    assert report["next_action"]["code"] == "TTAB_ACTION"


def test_ttab_accepted_enters_final_acceptance_phase():
    report = evaluate_lifecycle(_ttab("TTAB_ACCEPTED", ready=True))

    assert report["current_phase"] == "FINAL_ACCEPTANCE"
    assert report["status"] == "FINAL_ACCEPTANCE_REQUIRED"
    assert all(gate["accepted"] for gate in report["gates"].values())
    assert report["next_action"]["code"] == "RUN_FOUR_DOMAIN_ACCEPTANCE"


def test_build_lifecycle_threads_all_verification_flags():
    calls = []

    def builder(raw_root, **kwargs):
        calls.append((raw_root, kwargs))
        return _ttab("TTAB_PHASE_UNLOCKED", ready=False)

    report = build_lifecycle(
        Path("/raw"),
        expected_history_parts=91,
        deep_source_test=True,
        verify_us_source_files=True,
        verify_assignment_sources=True,
        verify_ttab_sources=True,
        persistent_worker_running=True,
        ttab_gate_builder=builder,
    )

    assert report["current_phase"] == "US_TTAB"
    assert calls == [
        (
            Path("/raw"),
            {
                "expected_history_parts": 91,
                "deep_source_test": True,
                "verify_us_source_files": True,
                "verify_assignment_sources": True,
                "verify_ttab_sources": True,
                "persistent_worker_running": True,
            },
        )
    ]


def test_invalid_expected_history_parts_is_rejected():
    try:
        build_lifecycle(Path("/raw"), expected_history_parts=0)
    except ValueError as exc:
        assert str(exc) == "expected_history_parts must be at least 1"
    else:
        raise AssertionError("expected ValueError")
