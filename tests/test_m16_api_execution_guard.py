import pytest
from fastapi import HTTPException

import app.main as main_module


def _guard(*, allowed: bool = True, mode: str = "REGISTERED_REPLAY_CONTINUATION"):
    return {
        "allowed": allowed,
        "guard_version": "TEST_GUARD",
        "mode": mode,
        "issues": [] if allowed else [{"type": "TEST_BLOCK"}],
    }


def test_api_scan_uses_guarded_registered_continuation(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(main_module, "build_execution_guard", lambda: _guard())
    monkeypatch.setattr(
        main_module,
        "scan_cn_incoming",
        lambda trigger_type: calls.append(("scan", trigger_type)) or {"registered": 1},
    )

    result = main_module.trigger_cn_scan()
    assert result == {"registered": 1}
    assert calls == [("scan", "MANUAL_API_GUARDED_SCAN")]


def test_api_run_uses_guarded_registered_continuation(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(main_module, "build_execution_guard", lambda: _guard())
    monkeypatch.setattr(
        main_module,
        "scan_and_ingest_cn",
        lambda trigger_type: calls.append(("run", trigger_type)) or {"ingest": {"success": 1}},
    )

    result = main_module.trigger_cn_cycle()
    assert result == {"ingest": {"success": 1}}
    assert calls == [("run", "MANUAL_API_GUARDED")]


@pytest.mark.parametrize("action", ["scan", "run"])
def test_api_mutation_cannot_bootstrap_clean_replay(action: str, monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "build_execution_guard",
        lambda: _guard(mode="CLEAN_RESET_FIRST_RUN"),
    )
    with pytest.raises(HTTPException) as exc_info:
        main_module._cn_api_execution_guard(action)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "CN_CLEAN_REPLAY_MANUAL_BOOTSTRAP_REQUIRED"
    assert "run-cn.ps1" in exc_info.value.detail["instruction"]


def test_api_mutation_surfaces_guard_block(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "build_execution_guard",
        lambda: _guard(allowed=False, mode="RETRY_REQUIRED"),
    )
    with pytest.raises(HTTPException) as exc_info:
        main_module._cn_api_execution_guard("run")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "CN_EXECUTION_GUARD_BLOCKED"
    assert exc_info.value.detail["guard"]["mode"] == "RETRY_REQUIRED"


def test_api_guard_failure_is_service_unavailable(monkeypatch) -> None:
    def fail_guard():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(main_module, "build_execution_guard", fail_guard)
    with pytest.raises(HTTPException) as exc_info:
        main_module._cn_api_execution_guard("scan")
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "CN_EXECUTION_GUARD_UNAVAILABLE"


def test_retry_api_remains_explicit_repair_path(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        main_module,
        "ingest_pending_cn",
        lambda **kwargs: calls.append(kwargs) or {"success": 1},
    )
    result = main_module.retry_cn_failed()
    assert result == {"success": 1}
    assert calls == [
        {
            "trigger_type": "MANUAL_API_RETRY",
            "include_failed": True,
            "limit": 1,
        }
    ]
