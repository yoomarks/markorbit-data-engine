from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-raw-bind-cutover-to-f.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "raw-bind-cutover-apply-runtime.yml"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_apply_is_exact_main_admin_and_preflight_gated() -> None:
    text = source()
    for marker in (
        "[switch]$Apply",
        "Assert-ExactMain 'entry'",
        "Assert-ExactMain 'exit'",
        "requires elevated Administrator PowerShell",
        "preflight-raw-bind-cutover-to-f.ps1",
        "decision=RAW_BIND_CUTOVER_PREFLIGHT_READY",
        "mandatory_preflight_ready=",
        "ready_for_apply=",
    ):
        assert marker in text


def test_apply_only_changes_joint_raw_env_aliases() -> None:
    text = source()
    for marker in (
        "RAW_DATA_PATH",
        "VISUAL_RAW_PATH",
        "VISUAL_PROCESSED_PATH",
        "F:/MarkOrbitData/raw",
        "Get-NonTargetEnvText",
        "Non-target .env content changed",
        "VISUAL_PROCESSED_PATH_CHANGED",
        "NON_TARGET_ENV_CONTENT_CHANGED",
        "proposed_VISUAL_PROCESSED_PATH='UNCHANGED'",
    ):
        assert marker in text
    assert "[System.IO.File]::WriteAllBytes($envPath, $envBytesAfter)" in text
    assert "[System.IO.File]::WriteAllBytes($envPath, $envBytesBefore)" in text


def test_apply_validates_compose_binds_without_starting_consumers() -> None:
    text = source()
    for marker in (
        "compose --profile mark-image --profile qcc config --format json",
        "'/data/raw'",
        "'/data/visual-raw'",
        "'/data/visual-processed'",
        "RAW_BIND_NOT_ON_F:",
        "VISUAL_RAW_BIND_NOT_ON_F:",
        "VISUAL_PROCESSED_BIND_CHANGED:",
        "api",
        "worker",
        "mark-image-worker",
        "qcc-acquisition",
        "RAW_CONSUMER_STATE_CHANGED_AFTER_ENV_UPDATE",
    ):
        assert marker in text
    for forbidden in (
        "compose','up",
        "compose','down",
        "compose','start",
        "compose','restart",
        "docker','restart",
    ):
        assert forbidden not in text


def test_apply_preserves_production_and_rolls_back_env_on_failure() -> None:
    text = source()
    for marker in (
        "Get-ProductionClickHouseHealth",
        "markorbit-data-engine_clickhouse_data",
        "PRODUCTION_INVARIANT_FAILED_AFTER_ENV_UPDATE",
        "automatic_env_rollback",
        "rollback_verified=",
        "Environment write failed and exact rollback could not be verified",
        "Post-cutover validation failed and exact .env rollback could not be verified",
    ):
        assert marker in text


def test_apply_forbids_data_and_platform_mutation() -> None:
    text = source()
    for forbidden in (
        "robocopy.exe",
        "Copy-Item",
        "Remove-Item",
        "Clear-Content",
        "Mount-VHD",
        "Dismount-VHD",
        "Resize-VHD",
        "New-VHD",
        "mkfs.ext4",
        "Format-Volume",
        "--shutdown",
        "--unregister",
        "docker','prune",
        "docker','volume','rm",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "2023_5.zip",
    ):
        assert forbidden not in text
    for marker in (
        "raw_delete_authorized=$false",
        "visual_processed_migration_authorized=$false",
        "docker_recreate_performed=$false",
        "docker_restart_performed=$false",
        "clickhouse_mutation_performed=$false",
        "accepted_volume_mutation_performed=$false",
        "vhdx_mutation_performed=$false",
        "wsl_mutation_performed=$false",
        "corpus_replay_performed=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ):
        assert marker in text


def test_apply_emits_explicit_decision_and_deletion_hold() -> None:
    text = source()
    for marker in (
        "RAW_BIND_CUTOVER_APPLY_V1",
        "RAW_BIND_CUTOVER_READY_FOR_APPLY",
        "RAW_BIND_CUTOVER_APPLY_GO",
        "RAW_BIND_CUTOVER_BLOCKED",
        "RAW_BIND_CUTOVER_DONE",
        "RAW_BIND_RUNTIME_PROBE",
        "RAW_BIND_RUNTIME_PROBE_NOT_YET_ACCEPTED",
        "VISUAL_PROCESSED_PATH_UNDER_LEGACY_D_RAW",
    ):
        assert marker in text


def test_workflow_has_server_side_concurrency() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "concurrency:" in text
    assert "github.workflow" in text
    assert "github.event.pull_request.number" in text
    assert "cancel-in-progress:" in text
