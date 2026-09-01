from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preflight-raw-bind-cutover-to-f.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "raw-bind-cutover-preflight-runtime.yml"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_preflight_is_exact_main_admin_and_read_only() -> None:
    text = source()
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "requires elevated Administrator PowerShell" in text
    assert "[switch]$Apply" not in text
    for marker in (
        "env_change_authorized=$false",
        "docker_recreate_authorized=$false",
        "raw_delete_authorized=$false",
        "raw_move_authorized=$false",
        "visual_processed_migration_authorized=$false",
        "vhdx_mutation_performed=$false",
        "wsl_mutation_performed=$false",
        "docker_restart_performed=$false",
        "clickhouse_mutation_performed=$false",
        "corpus_replay_performed=$false",
    ):
        assert marker in text


def test_preflight_requires_joint_raw_and_visual_raw_cutover() -> None:
    text = source()
    for marker in (
        "RAW_DATA_PATH",
        "VISUAL_RAW_PATH",
        "VISUAL_PROCESSED_PATH",
        "proposed_RAW_DATA_PATH=$proposedComposePath",
        "proposed_VISUAL_RAW_PATH=$proposedComposePath",
        "proposed_VISUAL_PROCESSED_PATH='UNCHANGED'",
        "F:/MarkOrbitData/raw",
        "VISUAL_RAW_NOT_ALIAS_OF_LEGACY_RAW",
        "VISUAL_PROCESSED_PATH_UNDER_LEGACY_D_RAW",
        "JOINT_RAW_BIND_CUTOVER_APPLY",
    ):
        assert marker in text


def test_preflight_freezes_compose_bind_contract() -> None:
    text = source()
    for marker in (
        "${RAW_DATA_PATH}:/data/raw",
        "${VISUAL_RAW_PATH:-./raw_data}:/data/visual-raw",
        "${VISUAL_PROCESSED_PATH:-./raw_data/visual_processed}:/data/visual-processed",
        "COMPOSE_RAW_BIND_CONTRACT_DRIFT",
        "COMPOSE_VISUAL_RAW_BIND_CONTRACT_DRIFT",
        "COMPOSE_VISUAL_PROCESSED_BIND_CONTRACT_DRIFT",
    ):
        assert marker in text
    assert "$rawBindCount -ne 4" in text
    assert "$visualRawBindCount -ne 3" in text
    assert "$visualProcessedBindCount -ne 3" in text


def test_preflight_requires_current_full_byte_parity() -> None:
    text = source()
    for marker in (
        "metadata_parity_exact=",
        "Get-FileHash -LiteralPath $sourceItem.full_path -Algorithm SHA256",
        "Get-FileHash -LiteralPath $targetItem.full_path -Algorithm SHA256",
        "sha256_progress=",
        "CURRENT_METADATA_PARITY_FAILED",
        "CURRENT_SHA256_PARITY_FAILED",
        "CURRENT_SHA256_VERIFIED_TOTALS_MISMATCH",
        "SOURCE_MANIFEST_CHANGED_DURING_PREFLIGHT",
        "TARGET_MANIFEST_CHANGED_DURING_PREFLIGHT",
    ):
        assert marker in text


def test_preflight_requires_all_raw_writers_quiescent_and_production_safe() -> None:
    text = source()
    for service in ("api", "worker", "mark-image-worker", "qcc-acquisition"):
        assert f"'{service}'" in text
    for marker in (
        "RAW_CONSUMER_CONTAINER_PROBE_FAILED",
        "RAW_CONSUMER_CONTAINER_RUNNING",
        "Get-ProductionClickHouseHealth",
        "PRODUCTION_CLICKHOUSE_NOT_HEALTHY",
        "markorbit-data-engine_clickhouse_data",
        "ACCEPTED_CLICKHOUSE_VOLUME_MISSING",
    ):
        assert marker in text


def test_preflight_forbids_cutover_and_destructive_mutations() -> None:
    text = source()
    forbidden = (
        "robocopy.exe",
        "Copy-Item",
        "Move-Item",
        "Remove-Item",
        "Clear-Content",
        "Set-Content -LiteralPath $envPath",
        "Mount-VHD",
        "Dismount-VHD",
        "Resize-VHD",
        "New-VHD",
        "mkfs.ext4",
        "Format-Volume",
        "--shutdown",
        "--unregister",
        "docker','restart",
        "docker','prune",
        "docker','volume','rm",
        "compose','up",
        "compose','down",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "2023_5.zip",
    )
    for marker in forbidden:
        assert marker not in text


def test_preflight_emits_explicit_decision_and_deletion_hold() -> None:
    text = source()
    for marker in (
        "RAW_BIND_CUTOVER_PREFLIGHT_V1",
        "RAW_BIND_CUTOVER_PREFLIGHT_READY",
        "RAW_BIND_CUTOVER_PREFLIGHT_BLOCKED",
        "RAW_BIND_CUTOVER_PREFLIGHT_DONE",
        "RAW_BIND_CUTOVER_NOT_YET_APPLIED",
        "d_source_delete_blocker=",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ):
        assert marker in text


def test_dotenv_reader_explicitly_accepts_blank_lines_and_empty_files() -> None:
    text = source()
    function_start = text.index("function Get-DotEnvValues")
    function_end = text.index("function Resolve-ConfiguredHostPath")
    function_text = text[function_start:function_end]
    assert "[AllowEmptyString()]" in function_text
    assert "[AllowEmptyCollection()]" in function_text


def test_workflow_has_server_side_concurrency() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "concurrency:" in text
    assert "github.workflow" in text
    assert "github.event.pull_request.number" in text
    assert "cancel-in-progress:" in text
