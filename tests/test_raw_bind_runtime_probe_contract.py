from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-raw-bind-runtime-probe.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "raw-bind-runtime-probe-runtime.yml"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_probe_is_exact_main_admin_and_apply_gated() -> None:
    text = source()
    assert "[switch]$Apply" in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "requires elevated Administrator PowerShell" in text
    assert "if ($Apply -and $readyForApply)" in text


def test_probe_requires_accepted_joint_cutover_and_safe_invariants() -> None:
    text = source()
    for marker in (
        "RAW_DATA_PATH",
        "VISUAL_RAW_PATH",
        "F:/MarkOrbitData/raw",
        "metadata_parity_exact=",
        "api",
        "worker",
        "mark-image-worker",
        "qcc-acquisition",
        "markorbit-data-engine_clickhouse_data",
        "production_clickhouse_ready_before=",
        "running_raw_consumer_count_before=",
        "compose_resolution_ready=",
    ):
        assert marker in text


def test_probe_uses_one_transient_api_container_without_dependencies_or_build() -> None:
    text = source()
    assert "'run','--detach','--no-deps'" in text
    assert "'--name',$probeName,'--entrypoint','python','api'" in text
    assert "import time; time.sleep(300)" in text
    assert "'--build'" not in text
    assert text.count("'run','--detach','--no-deps'") == 1
    assert "Invoke-NativeText 'docker' @('rm','-f',$probeName)" in text


def test_probe_verifies_real_runtime_mount_sources_and_read_only_walk() -> None:
    text = source()
    for marker in (
        "Normalize-DockerBindSource",
        "/data/raw",
        "/data/visual-raw",
        "/data/visual-processed",
        "/run/desktop/mnt/host/",
        "/host_mnt/",
        "/mnt/host/",
        "probe_stage=read_only_container_walk",
        "os.walk(root)",
        "os.path.getsize(p)",
        "runtime_mount_ready=",
        "runtime_read_ready=",
    ):
        assert marker in text


def test_probe_preserves_runtime_and_data_safety() -> None:
    text = source()
    for marker in (
        "raw_delete_authorized=$false",
        "raw_move_authorized=$false",
        "env_change_authorized=$false",
        "worker_start_authorized=$false",
        "docker_restart_performed=$false",
        "docker_recreate_performed=$false",
        "clickhouse_mutation_performed=$false",
        "vhdx_mutation_performed=$false",
        "wsl_mutation_performed=$false",
        "corpus_replay_performed=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
        "VISUAL_PROCESSED_PATH_UNDER_LEGACY_D_RAW",
        "LEGACY_D_RAW_CLEANUP_NOT_YET_PLANNED",
    ):
        assert marker in text
    for forbidden in (
        "robocopy.exe",
        "Copy-Item",
        "Move-Item",
        "Remove-Item",
        "Clear-Content",
        "Set-Content -LiteralPath $envPath",
        "WriteAllBytes($envPath",
        "Mount-VHD",
        "Dismount-VHD",
        "Resize-VHD",
        "New-VHD",
        "mkfs.ext4",
        "Format-Volume",
        "--shutdown",
        "--unregister",
        "compose','up",
        "compose','down",
        "compose','start",
        "compose','restart",
        "docker','restart",
        "docker','prune",
        "docker','volume','rm",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "2023_5.zip",
    ):
        assert forbidden not in text


def test_probe_emits_explicit_acceptance_and_next_gate() -> None:
    text = source()
    for marker in (
        "RAW_BIND_RUNTIME_PROBE_V1",
        "RAW_BIND_RUNTIME_PROBE_READY_FOR_APPLY",
        "RAW_BIND_RUNTIME_PROBE_GO",
        "RAW_BIND_RUNTIME_PROBE_BLOCKED",
        "RAW_BIND_RUNTIME_PROBE_DONE",
        "PRODUCTION_HOT_WARM_SIZING_PLAN",
        "runtime_probe_accepted=",
        "probe_container_removed=",
    ):
        assert marker in text


def test_workflow_has_server_side_concurrency() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "concurrency:" in text
    assert "github.workflow" in text
    assert "github.event.pull_request.number" in text
    assert "cancel-in-progress:" in text
