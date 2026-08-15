from __future__ import annotations

from pathlib import Path

import pytest

from app import admin_domain_tasks
from app.cn import full_replay


ROOT = Path(__file__).resolve().parents[1]


def _success(name: str) -> dict:
    return {
        "attempted": 1,
        "success": 1,
        "failed": 0,
        "skipped_missing": 0,
        "busy": False,
        "packages": [{"file_name": name, "status": "SUCCESS"}],
    }


def _empty() -> dict:
    return {
        "attempted": 0,
        "success": 0,
        "failed": 0,
        "skipped_missing": 0,
        "busy": False,
        "packages": [],
    }


def test_full_replay_can_disable_clean_start_without_scanning(monkeypatch) -> None:
    monkeypatch.setattr(
        full_replay,
        "build_execution_guard",
        lambda: {"allowed": True, "mode": "CLEAN_RESET_FIRST_RUN", "issues": []},
    )
    monkeypatch.setattr(
        full_replay,
        "scan_cn_incoming",
        lambda **_: pytest.fail("Admin continuation must never start a clean replay"),
    )

    code, summary = full_replay.run_full_replay(
        resume_failed=True,
        allow_clean_start=False,
        emit=lambda _: None,
    )

    assert code == 4
    assert summary["status"] == "BLOCKED"
    assert summary["reason"] == "CLEAN_START_DISABLED"


def test_full_replay_invokes_guard_before_every_retry_and_normal_attempt(monkeypatch) -> None:
    guards = iter(
        [
            {"allowed": False, "mode": "RETRY_REQUIRED", "issues": []},
            {"allowed": True, "mode": "REGISTERED_REPLAY_CONTINUATION", "issues": []},
        ]
    )
    monkeypatch.setattr(full_replay, "build_execution_guard", lambda: next(guards))
    results = iter([_success("2022_3.zip"), _success("2023_4.zip"), _empty()])
    monkeypatch.setattr(full_replay, "ingest_pending_cn", lambda **_: next(results))
    phases: list[str] = []

    code, summary = full_replay.run_full_replay(
        resume_failed=True,
        before_package=phases.append,
        emit=lambda _: None,
    )

    assert code == 0
    assert summary == {"status": "COMPLETE", "processed_total": 2}
    assert phases == ["RETRY", "NORMAL", "NORMAL"]


def test_cn_admin_continue_rechecks_storage_and_requires_final_checkpoint(monkeypatch) -> None:
    storage_calls: list[str] = []
    captured: dict[str, object] = {}

    def fake_storage():
        storage_calls.append("storage")
        return {"status": "PASS"}

    def fake_full_replay(**kwargs):
        captured.update(kwargs)
        before_package = kwargs["before_package"]
        before_package("RETRY")
        before_package("NORMAL")
        return 0, {"status": "COMPLETE", "processed_total": 2}

    monkeypatch.setattr(admin_domain_tasks, "_assert_storage_headroom", fake_storage)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_continuation_not_stopped",
        lambda _run_id, _domain: None,
    )
    monkeypatch.setattr(admin_domain_tasks.full_replay, "run_full_replay", fake_full_replay)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_cn_accepted",
        lambda: {
            "status": "PASS_WITH_WARNINGS",
            "ready_for_next_domain": True,
            "reasons": [],
            "summary": {
                "registered_package_count": 72,
                "active_stage_rows": 0,
            },
        },
    )

    result = admin_domain_tasks.execute_admin_domain_task(
        {
            "run_id": "11111111-1111-1111-1111-111111111111",
            "payload": {
                "domain": "CN",
                "action": "CONTINUE",
                "expected_history_parts": 0,
            },
        }
    )

    assert captured["resume_failed"] is True
    assert captured["allow_clean_start"] is False
    assert captured["trigger_type"] == "ADMIN_UI_CN_CONTINUE"
    assert storage_calls == ["storage", "storage", "storage"]
    assert result["action"] == "CONTINUE"
    assert result["gate_status"] == "PASS_WITH_WARNINGS"
    assert result["result"]["summary"]["processed_total"] == 2
    checkpoint = result["result"]["final_checkpoint"]
    assert checkpoint["ready_for_next_domain"] is True
    assert checkpoint["summary"]["registered_package_count"] == 72
    assert "readiness" not in checkpoint
    assert "acceptance" not in checkpoint


def test_cn_admin_continue_blocks_when_final_checkpoint_fails(monkeypatch) -> None:
    monkeypatch.setattr(admin_domain_tasks, "_assert_storage_headroom", lambda: {})
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_continuation_not_stopped",
        lambda _run_id, _domain: None,
    )
    monkeypatch.setattr(
        admin_domain_tasks.full_replay,
        "run_full_replay",
        lambda **_: (0, {"status": "COMPLETE", "processed_total": 10}),
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_cn_accepted",
        lambda: (_ for _ in ()).throw(
            admin_domain_tasks.DomainTaskBlocked(
                "US Application is blocked by CN final checkpoint: FAIL"
            )
        ),
    )

    with pytest.raises(admin_domain_tasks.DomainTaskBlocked, match="final checkpoint: FAIL"):
        admin_domain_tasks.execute_admin_domain_task(
            {
                "run_id": "11111111-1111-1111-1111-111111111111",
                "payload": {
                    "domain": "CN",
                    "action": "CONTINUE",
                    "expected_history_parts": 0,
                },
            }
        )


def test_continue_action_is_limited_to_full_replay_domains() -> None:
    with pytest.raises(ValueError, match="CN, US Application, and US Assignment"):
        admin_domain_tasks.queue_admin_domain_task(
            domain="US_TTAB",
            action="CONTINUE",
            expected_history_parts=91,
        )


def test_task_center_exposes_continuous_cn_replay_control() -> None:
    markup = (ROOT / "web" / "admin-jobs.html").read_text(encoding="utf-8")
    assert "连续推进" in markup
    assert "queueTask('CN','CONTINUE')" in markup
    assert "每包前都会重新检查存储空间" in markup
    assert "首次 clean replay 不会从这里启动" in markup
