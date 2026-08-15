from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.us import replay_executor


def test_execute_replay_runs_boundary_hook_before_package_mutation(monkeypatch) -> None:
    order: list[str] = []
    preflight_calls: list[int] = []
    finished: list[tuple[str, str]] = []

    @contextmanager
    def fake_guard():
        yield True

    step = {
        "sequence": 1,
        "package_kind": "HISTORY_PART",
        "partition_value": "1",
        "file_name": "history-1.zip",
        "path": "/raw/history-1.zip",
        "location": "incoming",
        "sha256": "abc",
        "registry_package_id": "11111111-1111-1111-1111-111111111111",
        "registry_status": "REGISTERED",
        "source_rank": 1,
        "action": "INGEST",
    }
    plans = iter(
        [
            {"status": "READY", "blockers": [], "remaining_count": 1},
            {"status": "READY", "blockers": [], "remaining_count": 1, "next_step": step},
            {"status": "COMPLETE", "blockers": [], "remaining_count": 0},
        ]
    )

    monkeypatch.setattr(replay_executor, "us_ingestion_guard", fake_guard)
    monkeypatch.setattr(replay_executor, "ensure_us_m1_schema", lambda: None)
    monkeypatch.setattr(replay_executor, "recover_interrupted_us_ingestions", lambda: [])
    monkeypatch.setattr(
        replay_executor,
        "build_preflight",
        lambda *args, **kwargs: preflight_calls.append(1) or {"safe_to_replay": True},
    )
    monkeypatch.setattr(
        replay_executor,
        "build_replay_plan",
        lambda *args, **kwargs: next(plans),
    )
    monkeypatch.setattr(replay_executor, "create_job_run", lambda **kwargs: "run-1")
    monkeypatch.setattr(
        replay_executor,
        "finish_job_run",
        lambda run_id, status, **kwargs: finished.append((run_id, status)),
    )

    def fake_discovered(_step):
        order.append("discover")
        return SimpleNamespace(path=Path("/raw/history-1.zip"))

    monkeypatch.setattr(replay_executor, "_discovered_package", fake_discovered)

    def fake_ingest(*args, **kwargs):
        order.append("ingest")
        return {"case_count": 1}

    monkeypatch.setattr(replay_executor, "ingest_us_package", fake_ingest)

    def before_package(current_step):
        assert current_step["sequence"] == 1
        assert order == []
        order.append("boundary")

    result = replay_executor.execute_replay(
        Path("/raw"),
        expected_history_parts=1,
        max_packages=None,
        before_package=before_package,
    )

    assert order == ["boundary", "discover", "ingest"]
    assert preflight_calls == [1]
    assert result["status"] == "COMPLETE"
    assert result["processed_count"] == 1
    assert result["source_preflight_runs"] == 1
    assert finished[-1] == ("run-1", "SUCCESS")


def test_boundary_hook_exception_prevents_next_package_mutation(monkeypatch) -> None:
    @contextmanager
    def fake_guard():
        yield True

    step = {
        "sequence": 2,
        "file_name": "daily.zip",
        "registry_status": "REGISTERED",
        "registry_package_id": "22222222-2222-2222-2222-222222222222",
        "action": "INGEST",
    }
    plans = iter(
        [
            {"status": "READY", "blockers": [], "remaining_count": 1},
            {"status": "READY", "blockers": [], "remaining_count": 1, "next_step": step},
        ]
    )

    monkeypatch.setattr(replay_executor, "us_ingestion_guard", fake_guard)
    monkeypatch.setattr(replay_executor, "ensure_us_m1_schema", lambda: None)
    monkeypatch.setattr(replay_executor, "recover_interrupted_us_ingestions", lambda: [])
    monkeypatch.setattr(replay_executor, "build_preflight", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        replay_executor,
        "build_replay_plan",
        lambda *args, **kwargs: next(plans),
    )
    monkeypatch.setattr(replay_executor, "create_job_run", lambda **kwargs: "run-2")
    monkeypatch.setattr(replay_executor, "finish_job_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        replay_executor,
        "_discovered_package",
        lambda _step: (_ for _ in ()).throw(AssertionError("source must not be opened")),
    )
    monkeypatch.setattr(
        replay_executor,
        "ingest_us_package",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not ingest")),
    )

    class BoundaryStop(RuntimeError):
        pass

    try:
        replay_executor.execute_replay(
            Path("/raw"),
            expected_history_parts=1,
            max_packages=None,
            before_package=lambda _step: (_ for _ in ()).throw(BoundaryStop("stop")),
        )
    except BoundaryStop as exc:
        assert str(exc) == "stop"
    else:
        raise AssertionError("boundary exception must escape without mutating the next package")
