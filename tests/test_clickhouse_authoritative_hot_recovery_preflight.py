from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts" / "prepare-clickhouse-authoritative-hot-recovery.ps1"


def _text() -> str:
    return PREFLIGHT.read_text(encoding="utf-8")


def test_preflight_freezes_exact_authoritative_regression_state() -> None:
    text = _text()
    assert '[string]$SourceHotPath = "E:\\MarkOrbitData\\hot\\clickhouse"' in text
    assert '[string]$RecoveryHotPath = "E:\\MarkOrbitData\\hot\\clickhouse-cs"' in text
    assert '[string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545"' in text
    assert "Active Hot source is not the frozen authoritative legacy root" in text
    assert "source_case_chain_all_disabled" in text
    assert "Recovery destination must remain absent during preflight" in text
    assert "AUTHORITATIVE_HOT_RECOVERY_PREFLIGHT_GO" in text


def test_preflight_requires_exact_main_idle_zero_worker_and_healthy_clickhouse() -> None:
    text = _text()
    assert "git status --porcelain" in text
    assert "git fetch origin main" in text
    assert "git rev-parse origin/main" in text
    assert "Exact-main mismatch" in text
    assert "stop-idle-worker.ps1" in text
    assert "-StopIdleWorker" not in text
    assert "docker compose ps -a -q worker" in text
    assert "Worker containers must be absent" in text
    assert "Exactly one running ClickHouse container is required" in text
    assert "ClickHouse must be healthy for preflight" in text
    assert ":24\\.8" in text


def test_preflight_measures_source_read_only_and_uses_copy_sized_headroom() -> None:
    text = _text()
    assert "type=bind,source=$source,target=/source,readonly" in text
    assert "find /source -type f -printf '%s\\n'" in text
    assert "source_regular_file_bytes" in text
    assert "measured_while_clickhouse_running = $true" in text
    assert "frozen_manifest = $false" in text
    assert "$requiredFreeBytes = $sourceRegularBytes + $reserveBytes" in text
    assert "recovery_headroom_ok=$headroomOk" in text
    assert "[int]$ReserveGiB = 128" in text


def test_preflight_captures_logical_and_environment_provenance() -> None:
    text = _text()
    assert "markorbit_facts.schema_version FINAL" in text
    assert "system.parts" in text
    assert "table_rows_sha256" in text
    assert "table_uuid_sha256" in text
    assert "com.docker.compose.project.config_files" in text
    assert "docker-compose\\.hot-cold-storage\\.yml" in text
    assert "GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'Process')" in text
    assert "GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'User')" in text
    assert "GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'Machine')" in text
    assert "local_env_hot_path_match_count" in text


def test_preflight_is_strictly_non_mutating_for_hot_storage_and_us() -> None:
    lowered = _text().lower()
    for forbidden in (
        "docker compose stop",
        "docker stop",
        "docker compose restart",
        "docker restart",
        "new-item -itemtype directory -path $destination",
        "fsutil.exe file setcasesensitiveinfo",
        "rename-item",
        "move-item",
        "remove-item",
        "cp -a /source/. /target/",
        "chmod ",
        "chown ",
        "apply-us-m1-schema.ps1",
        "run-us-capacity-pilot.ps1",
        "replay-us-deterministic.ps1",
        "2023_5.zip",
    ):
        assert forbidden not in lowered
    for marker in (
        "clickhouse_stop_performed = $false",
        "source_mutation_performed = $false",
        "destination_created = $false",
        "case_sensitivity_changed = $false",
        "copy_performed = $false",
        "schema_apply_performed = $false",
        "corpus_replay_performed = $false",
        "worker_start_performed = $false",
    ):
        assert marker in lowered


def test_preflight_uses_ps51_safe_object_arrays_not_generic_object_lists() -> None:
    text = _text()
    assert "System.Collections.Generic.List[object]" not in text
    assert "return @($rows)" not in text
    assert "[string]::Equals" in text
    assert "AUTHORITATIVE_HOT_RECOVERY_PREFLIGHT_FAILURE" in text
    assert "script_stack_trace=" in text
