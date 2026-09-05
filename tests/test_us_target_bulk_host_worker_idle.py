from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from app.us import target_bulk_host_worker_once


ROOT = Path(__file__).resolve().parents[1]


def test_one_claim_idle_is_success(monkeypatch) -> None:
    monkeypatch.setattr(target_bulk_host_worker_once, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        target_bulk_host_worker_once,
        "fail_closed_recover_target_bulk_tasks",
        lambda: {},
    )
    monkeypatch.setattr(target_bulk_host_worker_once.worker, "run_once", lambda: False)
    monkeypatch.setattr(sys, "argv", ["target_bulk_host_worker_once"])

    assert target_bulk_host_worker_once.main() == 0


def test_one_claim_real_failure_still_propagates(monkeypatch) -> None:
    monkeypatch.setattr(target_bulk_host_worker_once, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        target_bulk_host_worker_once,
        "fail_closed_recover_target_bulk_tasks",
        lambda: {},
    )

    def fail() -> bool:
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr(target_bulk_host_worker_once.worker, "run_once", fail)
    monkeypatch.setattr(sys, "argv", ["target_bulk_host_worker_once"])

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        target_bulk_host_worker_once.main()


def test_windows_launcher_routes_once_to_idle_safe_shim() -> None:
    launcher = (
        ROOT / "scripts" / "run-us-application-target-bulk-host-worker.ps1"
    ).read_text(encoding="utf-8")
    supervisor = (
        ROOT / "scripts" / "run-us-application-target-bulk-host-worker-supervisor.ps1"
    ).read_text(encoding="utf-8")

    assert "app.us.target_bulk_host_worker_once" in launcher
    assert "app.us.target_bulk_host_worker_v2" in launcher
    assert "one_claim_idle_is_success" in launcher
    assert "'-Once'" in supervisor
    assert "if ($workerExit -ne 0)" in supervisor
    assert "Start-Sleep -Seconds $PollSeconds" in supervisor
