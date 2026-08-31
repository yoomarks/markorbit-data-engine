from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "scripts" / "recover-clickhouse-bind-to-linux-volume.ps1"
CONTRACT = ROOT / "scripts" / "assert-clickhouse-active-hot-storage-contract.ps1"
PILOT = ROOT / "scripts" / "run-us-capacity-pilot-target-host.ps1"
PREPARE = ROOT / "scripts" / "prepare-us-capacity-pilot-target-host.ps1"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_recovery_freezes_exact_source_and_retained_linux_volume() -> None:
    t = text(RECOVERY)
    assert '[string]$CurrentHotPath = "E:\\MarkOrbitData\\hot\\clickhouse"' in t
    assert '[string]$RetainedVolume = "markorbit-data-engine_clickhouse_data"' in t
    assert '[string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545"' in t
    assert "Active Hot source" not in t or "Current Hot source drifted" in t
    assert "Retained Linux volume missing" in t
    assert "Retained volume is still referenced by container" in t


def test_recovery_is_fail_closed_before_authoritative_stop() -> None:
    t = text(RECOVERY)
    assert "stop-idle-worker.ps1" in t
    assert "Worker containers must be absent" in t
    assert "schema snapshot drifted" in t
    assert "nondefault_storage_policy_count" in t
    assert "recovery_headroom_ok" in t
    assert "Docker Linux-volume headroom is insufficient; no service was stopped" in t
    assert "git fetch origin main" in t
    assert "Exact-main drift before storage mutation" in t


def test_recovery_keeps_windows_source_readonly_and_uses_stopped_manifest() -> None:
    t = text(RECOVERY)
    assert 'type=bind,source=$source,target=/root,readonly' in t
    assert "recovery_stage=stop_clickhouse_and_freeze_source" in t
    assert "frozen_source_manifest_sha256" in t
    assert "type=bind,source=$source,target=/source,readonly" in t
    assert "find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +" in t
    assert "cp -a /source/. /target/" in t
    assert "STRUCTURAL_COPY_PARITY_OK" in t
    lowered = t.lower()
    assert "remove-item $source" not in lowered
    assert "rm -rf /source" not in lowered
    assert "chmod" not in lowered
    assert "chown" not in lowered


def test_recovery_activates_base_compose_linux_volume_and_retains_rollback_source() -> None:
    t = text(RECOVERY)
    assert "Start-LinuxVolumeClickHouse" in t
    assert "'compose','-f','docker-compose.yml','up','-d','--wait','--no-deps','--force-recreate','clickhouse'" in t
    assert "[string]$newHot.Type -ne 'volume'" in t
    assert "[string]$newHot.Name -ne $RetainedVolume" in t
    assert "rollback_source_retained=True" in t
    assert "windows_hot_bind_active=False" in t
    assert "ROLLBACK_TO_UNTOUCHED_WINDOWS_BIND_PASS" in t
    assert "docker-compose.hot-cold-storage.yml" in t


def test_recovery_verifies_logical_parity_tmp_cleanup_and_real_mergetree_commit() -> None:
    t = text(RECOVERY)
    assert "table_rows_sha256" in t
    assert "table_uuid_sha256" in t
    assert "Assert-LogicalEqual" in t
    assert "schema_version tmp_insert dirs remain" in t
    assert "ENGINE=MergeTree" in t
    assert "INSERT INTO" in t
    assert "timeout ${TimeoutSeconds}s clickhouse-client" in t
    assert "native_mergetree_commit_verified=True" in t
    assert "CLICKHOUSE_LINUX_VOLUME_RECOVERY_PASS" in t


def test_recovery_never_replays_corpus_or_applies_us_schema() -> None:
    lowered = text(RECOVERY).lower()
    for forbidden in (
        "apply-us-m1-schema.ps1",
        "run-us-capacity-pilot.ps1",
        "replay-us-deterministic.ps1",
        "2023_5.zip",
        " -all",
        "docker compose down -v",
        "docker volume rm",
    ):
        assert forbidden not in lowered
    assert "schema_apply_performed=False" in text(RECOVERY)
    assert "corpus_replay_performed=False" in text(RECOVERY)


def test_active_storage_contract_rejects_windows_bind_and_requires_named_volume() -> None:
    t = text(CONTRACT)
    assert 'CLICKHOUSE_ACTIVE_DATA_STORAGE_CONTRACT_V2' in t
    assert "ACTIVE_CLICKHOUSE_DATA_NOT_LINUX_VOLUME" in t
    assert "ACTIVE_CLICKHOUSE_DATA_VOLUME_NOT_ACCEPTED" in t
    assert '[string]$AcceptedVolume = "markorbit-data-engine_clickhouse_data"' in t
    assert "windows_host_bind_accepted = $false" in t
    assert "SCHEMA_VERSION_TMP_INSERT_PRESENT" in t
    assert "CLICKHOUSE_ACTIVE_DATA_STORAGE_CONTRACT_PASS" in t
    assert "fsutil.exe" not in t


def test_us_target_host_paths_use_linux_volume_contract_not_hot_bind_diagnostic() -> None:
    pilot = text(PILOT)
    prepare = text(PREPARE)
    for t in (pilot, prepare):
        assert "assert-clickhouse-active-hot-storage-contract.ps1" in t
        assert "diagnose-clickhouse-active-hot-permissions-v2.ps1" not in t
    assert "LINUX_DATA_VOLUME_PRE_SCHEMA_OK" in pilot
    assert "LINUX_DATA_VOLUME_POST_SCHEMA_OK" in pilot
    assert "pre-package-mutation" in pilot
    assert "windows_host_bind_accepted" in pilot
