from __future__ import annotations

from pathlib import Path

import pytest

from app import admin_domain_tasks


ROOT = Path(__file__).resolve().parents[1]


def _accepted_gate() -> dict:
    return {
        "status": "US_APPLICATION_ALREADY_ACCEPTED",
        "ready_for_us_application": True,
        "safe_to_start_us_replay": False,
        "reason_codes": [],
        "us_pipeline_state": "ACCEPTED",
    }


def test_us_application_continue_runs_deterministic_full_replay_then_acceptance(
    monkeypatch,
) -> None:
    calls: list[object] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_storage_headroom",
        lambda: calls.append("storage") or {"status": "PASS"},
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_cn_accepted",
        lambda: calls.append("cn") or {"status": "PASS"},
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "ensure_us_m1_schema",
        lambda: calls.append("schema"),
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_us_application_replay_unlocked",
        lambda raw_root, expected_history_parts: calls.append(
            ("start_gate", raw_root, expected_history_parts)
        )
        or {
            "status": "READY_FOR_US_APPLICATION_REPLAY",
            "safe_to_start_us_replay": True,
        },
    )

    def fake_replay(raw_root, **kwargs):
        captured["raw_root"] = raw_root
        captured.update(kwargs)
        return {
            "status": "COMPLETE",
            "executor_version": "US_DETERMINISTIC_REPLAY_V1",
            "processed_count": 7,
            "source_preflight_runs": 1,
            "final_plan": {"remaining_count": 0, "steps": ["do not store"]},
            "initial_plan": {"steps": ["do not store"]},
        }

    monkeypatch.setattr(admin_domain_tasks, "execute_us_replay", fake_replay)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_us_application_accepted",
        lambda raw_root, expected_history_parts: calls.append(
            ("final_gate", raw_root, expected_history_parts)
        )
        or _accepted_gate(),
    )

    result = admin_domain_tasks.execute_admin_domain_task(
        {
            "payload": {
                "domain": "US_APPLICATION",
                "action": "CONTINUE",
                "expected_history_parts": 91,
            }
        }
    )

    assert calls[:3] == ["storage", "cn", "schema"]
    assert captured["expected_history_parts"] == 91
    assert captured["max_packages"] is None
    assert captured["trigger_type"] == "ADMIN_UI_US_CONTINUE"
    assert result["gate_status"] == "US_APPLICATION_ALREADY_ACCEPTED"
    assert result["result"]["processed_count"] == 7
    assert result["result"]["remaining_count"] == 0
    assert result["result"]["source_preflight_runs"] == 1
    assert "initial_plan" not in result["result"]
    assert "final_plan" not in result["result"]
    final_gate = result["result"]["final_application_gate"]
    assert final_gate["ready_for_us_application"] is True
    assert final_gate["us_pipeline_state"] == "ACCEPTED"


def test_us_application_continue_noops_when_already_accepted(monkeypatch) -> None:
    monkeypatch.setattr(admin_domain_tasks, "_assert_storage_headroom", lambda: {})
    monkeypatch.setattr(admin_domain_tasks, "_assert_cn_accepted", lambda: {"status": "PASS"})
    monkeypatch.setattr(admin_domain_tasks, "ensure_us_m1_schema", lambda: None)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_us_application_replay_unlocked",
        lambda raw_root, expected_history_parts: _accepted_gate(),
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "execute_us_replay",
        lambda *args, **kwargs: pytest.fail("accepted corpus must not replay again"),
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_us_application_accepted",
        lambda raw_root, expected_history_parts: _accepted_gate(),
    )

    result = admin_domain_tasks.execute_admin_domain_task(
        {
            "payload": {
                "domain": "US_APPLICATION",
                "action": "CONTINUE",
                "expected_history_parts": 91,
            }
        }
    )

    assert result["result"]["already_accepted"] is True
    assert result["result"]["processed_count"] == 0
    assert result["gate_status"] == "US_APPLICATION_ALREADY_ACCEPTED"


def test_us_application_continue_stops_on_transition_gate_block(monkeypatch) -> None:
    monkeypatch.setattr(admin_domain_tasks, "_assert_storage_headroom", lambda: {})
    monkeypatch.setattr(admin_domain_tasks, "_assert_cn_accepted", lambda: {"status": "PASS"})
    monkeypatch.setattr(admin_domain_tasks, "ensure_us_m1_schema", lambda: None)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_us_application_replay_unlocked",
        lambda raw_root, expected_history_parts: (_ for _ in ()).throw(
            admin_domain_tasks.DomainTaskBlocked(
                "US Application replay transition gate blocked mutation: STAGING_REQUIRED"
            )
        ),
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "execute_us_replay",
        lambda *args, **kwargs: pytest.fail("blocked transition must not mutate"),
    )

    with pytest.raises(admin_domain_tasks.DomainTaskBlocked, match="STAGING_REQUIRED"):
        admin_domain_tasks.execute_admin_domain_task(
            {
                "payload": {
                    "domain": "US_APPLICATION",
                    "action": "CONTINUE",
                    "expected_history_parts": 91,
                }
            }
        )


def test_us_application_continue_requires_source_backed_acceptance(monkeypatch) -> None:
    monkeypatch.setattr(admin_domain_tasks, "_assert_storage_headroom", lambda: {})
    monkeypatch.setattr(admin_domain_tasks, "_assert_cn_accepted", lambda: {"status": "PASS"})
    monkeypatch.setattr(admin_domain_tasks, "ensure_us_m1_schema", lambda: None)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_us_application_replay_unlocked",
        lambda raw_root, expected_history_parts: {
            "status": "READY_FOR_US_APPLICATION_REPLAY",
            "safe_to_start_us_replay": True,
        },
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "execute_us_replay",
        lambda *args, **kwargs: {
            "status": "COMPLETE",
            "processed_count": 1,
            "source_preflight_runs": 1,
            "final_plan": {"remaining_count": 0},
        },
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_us_application_accepted",
        lambda raw_root, expected_history_parts: (_ for _ in ()).throw(
            admin_domain_tasks.DomainTaskBlocked(
                "US Application acceptance gate did not pass: SOURCE_VERIFICATION_REQUIRED"
            )
        ),
    )

    with pytest.raises(
        admin_domain_tasks.DomainTaskBlocked,
        match="SOURCE_VERIFICATION_REQUIRED",
    ):
        admin_domain_tasks.execute_admin_domain_task(
            {
                "payload": {
                    "domain": "US_APPLICATION",
                    "action": "CONTINUE",
                    "expected_history_parts": 91,
                }
            }
        )


def test_task_center_exposes_us_application_continuous_replay() -> None:
    markup = (ROOT / "web" / "admin-jobs.html").read_text(encoding="utf-8")
    assert "queueTask('US_APPLICATION','CONTINUE')" in markup
    assert "deterministic replay" in markup
    assert "source-backed acceptance" in markup
