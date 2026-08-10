from __future__ import annotations

from app.cn import full_replay


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


def test_clean_full_replay_scans_once_then_drains_registered_queue(monkeypatch):
    guards = iter(
        [
            {"allowed": True, "mode": "CLEAN_RESET_FIRST_RUN", "issues": []},
            {"allowed": True, "mode": "REGISTERED_REPLAY_CONTINUATION", "issues": []},
        ]
    )
    scan_calls = []
    ingest_calls = []
    results = iter([_success("1999.zip"), _success("2000.zip"), _empty()])

    monkeypatch.setattr(full_replay, "build_execution_guard", lambda: next(guards))
    monkeypatch.setattr(
        full_replay,
        "scan_cn_incoming",
        lambda trigger_type: scan_calls.append(trigger_type)
        or {"discovered": 2, "registered": 2, "duplicate": 0, "failed": 0},
    )

    def fake_ingest_pending_cn(*, trigger_type, include_failed, limit):
        ingest_calls.append((trigger_type, include_failed, limit))
        return next(results)

    monkeypatch.setattr(full_replay, "ingest_pending_cn", fake_ingest_pending_cn)
    events = []

    code, summary = full_replay.run_full_replay(emit=events.append)

    assert code == 0
    assert summary == {"status": "COMPLETE", "processed_total": 2}
    assert len(scan_calls) == 1
    assert ingest_calls == [
        ("MANUAL_FULL_CORPUS", False, 1),
        ("MANUAL_FULL_CORPUS", False, 1),
        ("MANUAL_FULL_CORPUS", False, 1),
    ]
    assert any(event.get("event") == "CN_FULL_REPLAY_DISCOVERY" for event in events)


def test_retry_barrier_is_not_crossed_without_explicit_resume(monkeypatch):
    guard = {
        "allowed": False,
        "mode": "RETRY_REQUIRED",
        "issues": [
            {
                "type": "FAILED_PACKAGE_MUST_BE_RETRIED_BEFORE_ADVANCE",
                "packages": [
                    {
                        "file_name": "2007.zip",
                        "status": "FAILED",
                        "error_message": "example failure",
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(full_replay, "build_execution_guard", lambda: guard)
    ingest_calls = []
    monkeypatch.setattr(
        full_replay,
        "ingest_pending_cn",
        lambda **kwargs: ingest_calls.append(kwargs),
    )

    code, summary = full_replay.run_full_replay(emit=lambda _: None)

    assert code == 4
    assert summary["status"] == "BLOCKED"
    assert summary["reason"] == "RETRY_REQUIRED"
    assert summary["guard"]["issues"][0]["packages"][0]["error_message"] == "example failure"
    assert ingest_calls == []


def test_resume_repairs_failed_package_before_normal_queue(monkeypatch):
    guards = iter(
        [
            {
                "allowed": False,
                "mode": "RETRY_REQUIRED",
                "issues": [
                    {
                        "type": "FAILED_PACKAGE_MUST_BE_RETRIED_BEFORE_ADVANCE",
                        "packages": [{"file_name": "2007.zip", "status": "FAILED"}],
                    }
                ],
            },
            {"allowed": True, "mode": "REGISTERED_REPLAY_CONTINUATION", "issues": []},
        ]
    )
    monkeypatch.setattr(full_replay, "build_execution_guard", lambda: next(guards))
    calls = []
    normal_results = iter([_success("2008.zip"), _empty()])

    def fake_ingest_pending_cn(*, trigger_type, include_failed, limit):
        calls.append((trigger_type, include_failed, limit))
        if include_failed:
            return _success("2007.zip")
        return next(normal_results)

    monkeypatch.setattr(full_replay, "ingest_pending_cn", fake_ingest_pending_cn)

    code, summary = full_replay.run_full_replay(
        resume_failed=True,
        emit=lambda _: None,
    )

    assert code == 0
    assert summary == {"status": "COMPLETE", "processed_total": 2}
    assert calls == [
        ("MANUAL_FULL_CORPUS_RETRY", True, 1),
        ("MANUAL_FULL_CORPUS", False, 1),
        ("MANUAL_FULL_CORPUS", False, 1),
    ]


def test_failed_result_preserves_exact_package_error(monkeypatch):
    monkeypatch.setattr(
        full_replay,
        "build_execution_guard",
        lambda: {"allowed": True, "mode": "REGISTERED_REPLAY_CONTINUATION", "issues": []},
    )
    failed = {
        "attempted": 1,
        "success": 0,
        "failed": 1,
        "skipped_missing": 0,
        "busy": False,
        "packages": [
            {
                "file_name": "2007.zip",
                "status": "FAILED",
                "error": "exact ClickHouse failure",
            }
        ],
    }
    monkeypatch.setattr(full_replay, "ingest_pending_cn", lambda **_: failed)
    events = []

    code, summary = full_replay.run_full_replay(emit=events.append)

    assert code == 2
    assert summary["status"] == "FAILED"
    assert summary["result"]["packages"][0]["error"] == "exact ClickHouse failure"
    package_event = next(event for event in events if event.get("event") == "CN_FULL_REPLAY_PACKAGE")
    assert package_event["packages"][0]["error"] == "exact ClickHouse failure"
