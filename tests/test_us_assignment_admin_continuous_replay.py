from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import admin_domain_tasks


ROOT = Path(__file__).resolve().parents[1]


def _accepted_gate() -> dict:
    return {
        "status": "ASSIGNMENT_ACCEPTED",
        "ready_for_assignment_phase": True,
        "assignment_ready": True,
        "assignment_state": "ACCEPTED",
        "reason_codes": [],
    }


def test_assignment_continue_drains_manifest_then_requires_verified_acceptance(
    monkeypatch,
) -> None:
    calls: list[object] = []
    captured: dict[str, object] = {}
    raw_root = Path("/raw")

    monkeypatch.setattr(admin_domain_tasks, "get_settings", lambda: SimpleNamespace(raw_data_root=raw_root))
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_storage_headroom",
        lambda: calls.append("storage") or {"status": "PASS"},
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_continuation_not_stopped",
        lambda run_id, domain: calls.append(("stop", run_id, domain)),
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_assignment_unlocked",
        lambda root, expected: calls.append(("start_gate", root, expected))
        or {
            "status": "ASSIGNMENT_PHASE_UNLOCKED",
            "ready_for_assignment_phase": True,
            "assignment_ready": False,
        },
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "ensure_assignment_schema",
        lambda: calls.append("schema"),
    )

    def fake_replay(manifest_path, root, **kwargs):
        captured["manifest_path"] = manifest_path
        captured["raw_root"] = root
        captured.update(kwargs)
        kwargs["before_package"]({"file_name": "historical.zip"})
        kwargs["before_package"]({"file_name": "daily.zip"})
        return {
            "mode": "APPLY",
            "replay_version": "US_ASSIGNMENT_MANIFEST_REPLAY_V1",
            "status": "COMPLETE",
            "processed_count": 2,
            "processed": [{"large": "do not persist"}],
            "final_plan": {"remaining_count": 0, "actions": ["do not persist"]},
            "source_preflight_runs": 1,
            "legal_ownership_conclusion": False,
        }

    monkeypatch.setattr(admin_domain_tasks, "execute_assignment_replay", fake_replay)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_assignment_accepted",
        lambda root, expected: calls.append(("final_gate", root, expected)) or _accepted_gate(),
    )

    result = admin_domain_tasks.execute_admin_domain_task(
        {
            "run_id": "run-assignment-123",
            "payload": {
                "domain": "US_ASSIGNMENT",
                "action": "CONTINUE",
                "expected_history_parts": 91,
            },
        }
    )

    assert calls[0] == "storage"
    assert ("start_gate", raw_root, 91) in calls
    assert "schema" in calls
    assert calls.count("storage") == 3
    assert ("stop", "run-assignment-123", "US_ASSIGNMENT") in calls
    assert captured["manifest_path"] == raw_root / "manifests" / "us_assignment" / "corpus.json"
    assert captured["raw_root"] == raw_root
    assert captured["apply"] is True
    assert captured["all_packages"] is True
    assert captured["resume_failed"] is True
    assert callable(captured["before_package"])
    assert result["gate_status"] == "ASSIGNMENT_ACCEPTED"
    replay = result["result"]
    assert replay["processed_count"] == 2
    assert replay["remaining_count"] == 0
    assert replay["source_preflight_runs"] == 1
    assert replay["legal_ownership_conclusion"] is False
    assert "processed" not in replay
    assert "final_plan" not in replay
    final_gate = replay["final_assignment_gate"]
    assert final_gate["assignment_ready"] is True
    assert final_gate["assignment_state"] == "ACCEPTED"
    assert final_gate["legal_ownership_conclusion"] is False
    assert "assignment_readiness" not in final_gate


def test_assignment_continue_noops_when_already_accepted(monkeypatch) -> None:
    raw_root = Path("/raw")
    monkeypatch.setattr(admin_domain_tasks, "get_settings", lambda: SimpleNamespace(raw_data_root=raw_root))
    monkeypatch.setattr(admin_domain_tasks, "_assert_storage_headroom", lambda: {})
    monkeypatch.setattr(admin_domain_tasks, "_assert_assignment_unlocked", lambda root, expected: _accepted_gate())
    monkeypatch.setattr(admin_domain_tasks, "ensure_assignment_schema", lambda: None)
    monkeypatch.setattr(
        admin_domain_tasks,
        "execute_assignment_replay",
        lambda *args, **kwargs: pytest.fail("accepted Assignment corpus must not replay again"),
    )
    monkeypatch.setattr(admin_domain_tasks, "_assert_assignment_accepted", lambda root, expected: _accepted_gate())

    result = admin_domain_tasks.execute_admin_domain_task(
        {
            "payload": {
                "domain": "US_ASSIGNMENT",
                "action": "CONTINUE",
                "expected_history_parts": 91,
            }
        }
    )

    assert result["result"]["already_accepted"] is True
    assert result["result"]["processed_count"] == 0
    assert result["result"]["legal_ownership_conclusion"] is False
    assert result["gate_status"] == "ASSIGNMENT_ACCEPTED"


def test_assignment_continue_stops_when_application_transition_is_blocked(monkeypatch) -> None:
    raw_root = Path("/raw")
    monkeypatch.setattr(admin_domain_tasks, "get_settings", lambda: SimpleNamespace(raw_data_root=raw_root))
    monkeypatch.setattr(admin_domain_tasks, "_assert_storage_headroom", lambda: {})
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_assignment_unlocked",
        lambda root, expected: (_ for _ in ()).throw(
            admin_domain_tasks.DomainTaskBlocked(
                "US Assignment transition gate blocked mutation: BLOCKED_BY_US_APPLICATION"
            )
        ),
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "execute_assignment_replay",
        lambda *args, **kwargs: pytest.fail("blocked transition must not mutate Assignment"),
    )

    with pytest.raises(admin_domain_tasks.DomainTaskBlocked, match="BLOCKED_BY_US_APPLICATION"):
        admin_domain_tasks.execute_admin_domain_task(
            {
                "payload": {
                    "domain": "US_ASSIGNMENT",
                    "action": "CONTINUE",
                    "expected_history_parts": 91,
                }
            }
        )


def test_assignment_continue_requires_source_verified_final_acceptance(monkeypatch) -> None:
    raw_root = Path("/raw")
    monkeypatch.setattr(admin_domain_tasks, "get_settings", lambda: SimpleNamespace(raw_data_root=raw_root))
    monkeypatch.setattr(admin_domain_tasks, "_assert_storage_headroom", lambda: {})
    monkeypatch.setattr(admin_domain_tasks, "_assert_continuation_not_stopped", lambda *_: None)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_assignment_unlocked",
        lambda root, expected: {
            "status": "ASSIGNMENT_PHASE_UNLOCKED",
            "ready_for_assignment_phase": True,
            "assignment_ready": False,
        },
    )
    monkeypatch.setattr(admin_domain_tasks, "ensure_assignment_schema", lambda: None)

    def fake_replay(*args, **kwargs):
        kwargs["before_package"]({"file_name": "assignment.zip"})
        return {
            "status": "COMPLETE",
            "processed_count": 1,
            "source_preflight_runs": 1,
            "final_plan": {"remaining_count": 0},
            "legal_ownership_conclusion": False,
        }

    monkeypatch.setattr(admin_domain_tasks, "execute_assignment_replay", fake_replay)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_assignment_accepted",
        lambda root, expected: (_ for _ in ()).throw(
            admin_domain_tasks.DomainTaskBlocked(
                "US Assignment acceptance gate did not pass: ASSIGNMENT_PHASE_UNLOCKED"
            )
        ),
    )

    with pytest.raises(admin_domain_tasks.DomainTaskBlocked, match="ASSIGNMENT_PHASE_UNLOCKED"):
        admin_domain_tasks.execute_admin_domain_task(
            {
                "run_id": "run-assignment-final",
                "payload": {
                    "domain": "US_ASSIGNMENT",
                    "action": "CONTINUE",
                    "expected_history_parts": 91,
                },
            }
        )


def test_assignment_final_acceptance_enables_both_source_verifications(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_gate(raw_root, expected_history_parts, **kwargs):
        captured["raw_root"] = raw_root
        captured["expected_history_parts"] = expected_history_parts
        captured.update(kwargs)
        return _accepted_gate()

    monkeypatch.setattr(admin_domain_tasks, "_build_assignment_gate", fake_gate)
    report = admin_domain_tasks._assert_assignment_accepted(Path("/raw"), 91)

    assert report["status"] == "ASSIGNMENT_ACCEPTED"
    assert captured["verify_us_source_files"] is True
    assert captured["verify_assignment_sources"] is True


def test_task_center_exposes_assignment_continuous_replay() -> None:
    markup = (ROOT / "web" / "admin-jobs.html").read_text(encoding="utf-8")
    assert "queueTask('US_ASSIGNMENT','CONTINUE')" in markup
    assert "queueTask('US_ASSIGNMENT','STOP')" in markup
    assert "manifests/us_assignment/corpus.json" in markup
    assert "recorded assignment facts" in markup
    assert "legal title / ownership conclusion" in markup
