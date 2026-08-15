from __future__ import annotations

from pathlib import Path

import pytest

from app import admin_domain_tasks


ROOT = Path(__file__).resolve().parents[1]


class _FakeCursor:
    def __init__(self, task_status: str, domain: str):
        self.task_status = task_status
        self.domain = domain
        self.current = None
        self.executions: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executions.append((sql, params))
        if "SELECT run_id, job_type, status, started_at, payload" in sql:
            self.current = {
                "run_id": "11111111-1111-1111-1111-111111111111",
                "job_type": f"{self.domain}_ADMIN_CONTINUE",
                "status": self.task_status,
                "started_at": None,
                "payload": {
                    "task_kind": "DOMAIN_CONTROL",
                    "domain": self.domain,
                    "action": "CONTINUE",
                },
            }
        elif "UPDATE control.job_run" in sql:
            status = "INTERRUPTED" if self.task_status == "QUEUED" else "RUNNING"
            self.current = {
                "run_id": "11111111-1111-1111-1111-111111111111",
                "job_type": f"{self.domain}_ADMIN_CONTINUE",
                "trigger_type": "ADMIN_UI",
                "status": status,
                "started_at": None,
                "finished_at": None,
                "payload": {"stop_requested": True},
                "error_message": "stop requested",
            }

    def fetchone(self):
        return self.current


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


@pytest.mark.parametrize("domain", ["CN", "US_APPLICATION", "US_ASSIGNMENT", "US_TTAB"])
@pytest.mark.parametrize(
    ("task_status", "expected_status"),
    [("QUEUED", "INTERRUPTED"), ("RUNNING", "RUNNING")],
)
def test_stop_request_is_cooperative_and_preserves_current_package(
    monkeypatch, domain: str, task_status: str, expected_status: str
) -> None:
    cursor = _FakeCursor(task_status, domain)
    conn = _FakeConn(cursor)
    monkeypatch.setattr(admin_domain_tasks, "postgres_conn", lambda: conn)

    result = admin_domain_tasks.request_admin_domain_stop(domain=domain)

    assert result["accepted"] is True
    assert result["task"]["status"] == expected_status
    select_params = next(
        params
        for sql, params in cursor.executions
        if "SELECT run_id, job_type, status, started_at, payload" in sql
    )
    assert domain in select_params
    update_sql = next(sql for sql, _ in cursor.executions if "UPDATE control.job_run" in sql)
    assert "stop_requested" in update_sql
    if task_status == "QUEUED":
        assert "status = 'INTERRUPTED'" in update_sql
    else:
        assert "waiting for the current package boundary" in update_sql


def test_stop_rejects_unknown_domain() -> None:
    with pytest.raises(ValueError, match="trademark continuous replay domains"):
        admin_domain_tasks.request_admin_domain_stop(domain="EU")


def test_cn_continuation_checks_stop_and_storage_before_next_package(monkeypatch) -> None:
    calls: list[str] = []

    def fake_runner(**kwargs):
        kwargs["before_package"]("NORMAL")
        return 0, {"status": "COMPLETE", "processed_total": 1}

    monkeypatch.setattr(admin_domain_tasks.full_replay, "run_full_replay", fake_runner)
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_continuation_not_stopped",
        lambda run_id, domain: calls.append(f"stop:{domain}:{run_id}"),
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "_assert_storage_headroom",
        lambda: calls.append("storage") or {},
    )

    result = admin_domain_tasks._run_cn_continuation("run-123")

    assert calls == ["stop:CN:run-123", "storage"]
    assert result["summary"]["processed_total"] == 1


def test_interrupted_continuation_finishes_as_interrupted(monkeypatch) -> None:
    finished: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        admin_domain_tasks,
        "execute_admin_domain_task",
        lambda task: (_ for _ in ()).throw(
            admin_domain_tasks.DomainTaskInterrupted("operator stop")
        ),
    )
    monkeypatch.setattr(
        admin_domain_tasks,
        "finish_job_run",
        lambda run_id, status, metrics=None, error_message=None: finished.append(
            (run_id, status, error_message)
        ),
    )

    admin_domain_tasks.finish_admin_domain_task({"run_id": "run-456"})

    assert finished == [("run-456", "INTERRUPTED", "operator stop")]


def test_worker_restart_does_not_requeue_stop_requested_task() -> None:
    source = (ROOT / "app" / "admin_domain_tasks.py").read_text(encoding="utf-8")
    assert "payload->>'stop_requested' = 'true'" in source
    assert "Worker restarted after stop was requested; task not requeued." in source


def test_task_api_and_ui_expose_safe_stop_for_continuous_domains() -> None:
    api = (ROOT / "app" / "admin_task_api.py").read_text(encoding="utf-8")
    markup = (ROOT / "web" / "admin-jobs.html").read_text(encoding="utf-8")
    assert 'action.strip().upper() == "STOP"' in api
    assert "request_admin_domain_stop" in api
    assert "queueTask('CN','STOP')" in markup
    assert "queueTask('US_APPLICATION','STOP')" in markup
    assert "queueTask('US_ASSIGNMENT','STOP')" in markup
    assert "queueTask('US_TTAB','STOP')" in markup
    assert "停止连续推进" in markup
    assert "安全边界停止" in markup
