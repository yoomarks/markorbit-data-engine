from __future__ import annotations

from pathlib import Path

import pytest

from app import admin_domain_tasks


ROOT = Path(__file__).resolve().parents[1]


def test_admin_domain_task_routes_and_ui_are_registered() -> None:
    import app.main as main

    routes = {route.path for route in main.app.routes}
    assert "/api/admin/v2/domain-tasks/{domain}/{action}" in routes

    markup = (ROOT / "web" / "admin-jobs.html").read_text(encoding="utf-8")
    assert "商标数据引擎任务控制" in markup
    assert "执行下一包" in markup
    assert "恢复失败" in markup
    assert "Expected History Parts" in markup
    assert "/api/admin/v2/domain-tasks/" in markup
    assert "QUEUED" in markup
    assert "BLOCKED" in markup


def test_us_admin_domain_tasks_require_pinned_history_part_count() -> None:
    with pytest.raises(ValueError, match="expected_history_parts"):
        admin_domain_tasks.queue_admin_domain_task(
            domain="US_APPLICATION",
            action="RUN",
            expected_history_parts=0,
        )


def test_admin_us_application_run_keeps_cn_and_storage_gates(monkeypatch) -> None:
    calls: list[object] = []
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
        "scan_and_ingest_us",
        lambda trigger_type: calls.append(trigger_type)
        or {"scan": {}, "ingest": {"busy": False, "failed": 0, "success": 1}},
    )

    result = admin_domain_tasks.execute_admin_domain_task(
        {
            "payload": {
                "domain": "US_APPLICATION",
                "action": "RUN",
                "expected_history_parts": 91,
            }
        }
    )

    assert calls[:3] == ["storage", "cn", "schema"]
    assert "ADMIN_UI_US" in calls
    assert result["gate_status"] == "PASS"


def test_admin_us_application_retry_uses_failed_package_path(monkeypatch) -> None:
    monkeypatch.setattr(admin_domain_tasks, "_assert_storage_headroom", lambda: {})
    monkeypatch.setattr(admin_domain_tasks, "_assert_cn_accepted", lambda: {"status": "PASS"})
    monkeypatch.setattr(admin_domain_tasks, "ensure_us_m1_schema", lambda: None)
    captured: dict[str, object] = {}

    def fake_retry(**kwargs):
        captured.update(kwargs)
        return {"busy": False, "failed": 0, "success": 1}

    monkeypatch.setattr(admin_domain_tasks, "ingest_pending_us", fake_retry)

    admin_domain_tasks.execute_admin_domain_task(
        {
            "payload": {
                "domain": "US_APPLICATION",
                "action": "RETRY",
                "expected_history_parts": 91,
            }
        }
    )

    assert captured["include_failed"] is True
    assert captured["limit"] == 1


def test_admin_downstream_tasks_keep_transition_gates() -> None:
    source = (ROOT / "app" / "admin_domain_tasks.py").read_text(encoding="utf-8")
    assert "build_headroom_report()" in source
    assert "shutil.disk_usage(raw_root)" in source
    assert "build_final_checkpoint(persistent_worker_running=False)" in source
    assert "build_assignment_gate(" in source
    assert "ready_for_assignment_phase" in source
    assert "build_ttab_gate(" in source
    assert "ready_for_ttab_phase" in source
    assert "ensure_assignment_schema()" in source
    assert "ensure_ttab_schema()" in source


def test_worker_recovers_and_executes_admin_queue_before_scheduled_cn() -> None:
    source = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    assert "recover_interrupted_admin_domain_tasks()" in source
    assert "with engine_mutation_guard() as acquired" in source
    assert "claim_next_admin_domain_task()" in source
    assert "finish_admin_domain_task(task)" in source
    assert source.index("claim_next_admin_domain_task()") < source.index("_run_scheduled_cn(logger)", source.index("while True"))
    assert "_ADMIN_POLL_SECONDS = 2.0" in source
