from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "manage-us-application-target-bulk-host-worker-task.ps1"
SUPERVISOR = ROOT / "scripts" / "run-us-application-target-bulk-host-worker-supervisor.ps1"
LAUNCHER = ROOT / "scripts" / "run-us-application-target-bulk-host-worker.ps1"


def test_scheduled_task_manager_is_exact_main_and_clean_tree_guarded() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert "git branch --show-current" in source
    assert "git status --porcelain" in source
    assert "git fetch origin main --quiet" in source
    assert "git rev-parse HEAD" in source
    assert "git rev-parse origin/main" in source
    assert "Local main must exactly equal origin/main" in source


def test_scheduled_task_runs_current_user_supervisor_with_restart_and_no_overlap() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert "Register-ScheduledTask" in source
    assert "New-ScheduledTaskTrigger -AtLogOn" in source
    assert "New-ScheduledTaskPrincipal" in source
    assert "-LogonType Interactive" in source
    assert "-RunLevel Highest" in source
    assert "-RestartCount 999" in source
    assert "-RestartInterval (New-TimeSpan -Minutes 1)" in source
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in source
    assert "-MultipleInstances IgnoreNew" in source
    assert "run-us-application-target-bulk-host-worker-supervisor.ps1" in source
    assert "run-us-application-target-bulk-host-worker.ps1" in source


def test_scheduled_task_manager_never_creates_or_approves_bulk_plan() -> None:
    source = MANAGER.read_text(encoding="utf-8")
    lowered = source.lower()

    assert "automatic_plan_approval=False" in source
    assert "production_mutation_requires_prepared_plan_and_explicit_approval=True" in source
    assert "/approve" not in lowered
    assert "plan-production-us-application-bulk-replay.ps1" not in lowered
    assert "run-production-us-application-bulk-replay.ps1" not in lowered
    assert "target_bulk_cli" not in lowered
    assert " -apply" not in lowered


def test_supervisor_reloads_v2_worker_through_one_claim_child_processes() -> None:
    supervisor = SUPERVISOR.read_text(encoding="utf-8")
    launcher = LAUNCHER.read_text(encoding="utf-8")

    assert "run-us-application-target-bulk-host-worker.ps1" in supervisor
    assert "'-Once'" in supervisor
    assert "& $PowerShellExe @arguments" in supervisor
    assert "Start-Sleep -Seconds $PollSeconds" in supervisor
    assert "Task Scheduler can apply its bounded restart policy" in supervisor
    assert "automatic_plan_approval=False" in supervisor
    assert "target_bulk_host_worker_v2" in launcher
    assert "target_bulk_host_worker.py" not in launcher


def test_manager_exposes_idempotent_lifecycle_actions() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    for action in ("Install", "Status", "Start", "Stop", "Restart", "Uninstall"):
        assert f"'{action}'" in source
    assert "Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force" in source
    assert "Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false" in source
