from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_host_restart_blocks_approved_mutation_instead_of_blind_requeue() -> None:
    control = (ROOT / "app" / "us" / "target_bulk_task_control.py").read_text(
        encoding="utf-8"
    )
    host = (ROOT / "app" / "us" / "target_bulk_host_worker.py").read_text(
        encoding="utf-8"
    )

    assert "fail_closed_recover_target_bulk_tasks" in host
    assert "recover_interrupted_target_bulk_tasks" not in host
    assert "elif payload.get(\"approved_plan_sha256\")" in control
    assert "status = STATUS_BLOCKED" in control
    assert "Automatic replay is forbidden" in control
    assert "STATUS_PREPARE_QUEUED" in control


def test_resume_surface_remains_explicit_after_fail_closed_restart() -> None:
    control = (ROOT / "app" / "us" / "target_bulk_task_control.py").read_text(
        encoding="utf-8"
    )
    assert "_RESUMABLE = {STATUS_BLOCKED, STATUS_FAILED, STATUS_INTERRUPTED}" in control
    assert "resume_requested_at" in control
    assert "prepared US target bulk plan still requires explicit operator approval" in control
