from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREP = ROOT / "scripts" / "prepare-docker-data-reset.ps1"
STOP = ROOT / "scripts" / "stop-databases-for-docker-data-reset.ps1"
RESTORE = ROOT / "scripts" / "restore-after-docker-data-reset.ps1"


def test_prepare_operator_is_non_destructive_and_freezes_recovery_evidence() -> None:
    text = PREP.read_text(encoding="utf-8")
    lowered = text.lower()

    for marker in (
        "ADMINISTRATOR_OK",
        "POSTGRES_DUAL_BACKUP_REVERIFIED",
        "CN_PRE_RESET_EVIDENCE_OK",
        "DOCKER_SETTINGS_BACKUP_OK",
        "ANCILLARY_VOLUMES_BACKUP_OK",
        "DOCKER_DATA_RESET_PREPARATION_V1",
        "DOCKER_DATA_RESET_PREPARATION_OK",
        "markorbit-local_*",
        "settings-store.json",
        "cn_active_rows",
        "cn_active_bytes",
    ):
        assert marker in text

    for forbidden in (
        "docker desktop stop",
        "docker desktop restart",
        "wsl --shutdown",
        "diskpart",
        "optimize-vhd",
        "docker volume rm",
        "docker system prune",
        "docker compose down",
        "remove-item",
        "reset to factory",
        "clean up data",
    ):
        assert forbidden not in lowered


def test_clean_stop_operator_only_stops_databases_after_preparation() -> None:
    text = STOP.read_text(encoding="utf-8")
    lowered = text.lower()

    for marker in (
        "PREPARATION_MANIFEST_OK",
        "DATABASES_HEALTHY_BEFORE_STOP",
        "docker stop --timeout 60 $PostgresContainer",
        "docker stop --timeout 120 $ClickHouseContainer",
        "POSTGRES_CLEAN_STOP_OK",
        "CLICKHOUSE_CLEAN_STOP_OK",
        "DATABASES_CLEANLY_STOPPED_FOR_DOCKER_RESET",
    ):
        assert marker in text

    for forbidden in (
        "docker desktop stop",
        "docker desktop restart",
        "wsl --shutdown",
        "diskpart",
        "optimize-vhd",
        "docker volume rm",
        "docker system prune",
        "docker compose down",
        "remove-item",
    ):
        assert forbidden not in lowered


def test_restore_operator_requires_fresh_d_drive_and_preserves_external_clickhouse() -> None:
    text = RESTORE.read_text(encoding="utf-8")
    lowered = text.lower()

    for marker in (
        "DOCKER_ENGINE_OK",
        "PREPARATION_MANIFEST_OK",
        "LOGICAL_BACKUP_RECEIPT_OK",
        "COLD_PGDATA_RECEIPT_OK",
        "RECOVERY_RECEIPT_CHAIN_OK",
        "HOT_CASE_SENSITIVE_QUERY_OK",
        "FRESH_DOCKER_DATA_DISK_OK",
        "CLEAN_DOCKER_STATE_OK",
        "COMPOSE_MODEL_OK",
        "CREATED_MOUNTS_OK",
        "POSTGRES_PGDATA_RESTORED",
        "ANCILLARY_VOLUMES_RESTORED",
        "DATABASES_HEALTHY",
        "POSTGRES_INVENTORY_OK",
        "CN_ROW_AND_BYTE_EQUIVALENCE_OK",
        "API_WORKER_STOPPED_OK",
        "DOCKER_DATA_RESET_RESTORE_OK",
        "D:\\DockerData\\DockerDesktopWSL",
        "E:\\MarkOrbitData\\hot\\clickhouse",
        "F:\\MarkOrbitData\\cold\\clickhouse",
    ):
        assert marker in text

    assert "docker compose @compose create postgres clickhouse" in text
    assert "docker compose @compose pull postgres clickhouse" in text
    assert "markorbit-data-engine_postgres_data" in text
    assert "docker volume create $volumeName" in text
    assert "tar -xzf" in text
    assert "postgres_logical_sha256" in text
    assert "postgres_pgdata_sha256" in text
    assert "2%" in text

    for forbidden in (
        "get-filehash",
        "--no-deps",
        "docker volume rm",
        "docker system prune",
        "docker compose down",
        "remove-item",
        "diskpart",
        "optimize-vhd",
        "wsl --shutdown",
        "docker desktop stop",
        "docker desktop restart",
    ):
        assert forbidden not in lowered


def test_restore_receipt_gate_is_fail_closed_without_duplicate_full_file_hashing() -> None:
    text = RESTORE.read_text(encoding="utf-8")

    assert "function Assert-FileReceipt" in text
    assert "Recovery file byte length changed after preparation" in text
    assert "Recovery SHA256 receipt chain mismatch" in text
    assert "function Assert-AncillaryReceipt" in text
    assert "Ancillary recovery file byte length changed after preparation" in text
    assert "^[0-9a-fA-F]{64}$" in text
    assert "Get-FileHash" not in text


def test_reset_operators_use_character_path_normalization_and_native_shell_quotes() -> None:
    prep = PREP.read_text(encoding="utf-8")
    restore = RESTORE.read_text(encoding="utf-8")

    assert ".Replace([char]92, [char]47)" in prep
    assert ".Replace([char]92, [char]47)" in restore
    assert ".Replace([char]47, [char]92)" in restore
    assert "-replace '\\'" not in prep
    assert "-replace '\\'" not in restore

    # PowerShell single-quoted sh -lc payloads may contain normal double quotes,
    # but a literal backslash followed by a quote would be passed through to sh.
    assert '\\"' not in prep
    assert '\\"' not in restore
