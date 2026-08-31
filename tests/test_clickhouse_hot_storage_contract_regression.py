from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "scripts" / "assert-clickhouse-active-hot-storage-contract.ps1"
AUDIT = ROOT / "scripts" / "audit-clickhouse-hot-path-regression.ps1"
HOT_V2 = ROOT / "scripts" / "diagnose-clickhouse-active-hot-permissions-v2.ps1"
ENV_EXAMPLE = ROOT / ".env.example"
OVERRIDE = ROOT / "docker-compose.hot-cold-storage.yml"


def test_active_storage_contract_requires_linux_named_volume() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    assert 'markorbit-data-engine_clickhouse_data' in text
    assert "ACTIVE_CLICKHOUSE_DATA_NOT_LINUX_VOLUME" in text
    assert "ACTIVE_CLICKHOUSE_DATA_VOLUME_NOT_ACCEPTED" in text
    assert "SCHEMA_VERSION_TMP_INSERT_PRESENT" in text
    assert "windows_host_bind_accepted = $false" in text
    assert "{{json .Mounts}}" in text
    assert "/var/lib/clickhouse" in text
    assert "CLICKHOUSE_ACTIVE_DATA_STORAGE_CONTRACT_PASS" in text
    assert "fsutil.exe" not in text


def test_hot_v2_remains_historical_diagnostic_but_us_no_longer_depends_on_it() -> None:
    text = HOT_V2.read_text(encoding="utf-8")
    assert "diagnose-clickhouse-active-hot-permissions.ps1" in text
    assert "ACTIVE_HOT_PERMISSION_DIAGNOSTIC_V2_COMPLETE" in text


def test_read_only_regression_audit_never_authorizes_switch() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "safe_to_switch = $false" in lowered
    assert "filesystem_mutation_performed = $false" in lowered
    assert "corpus_replay_performed = $false" in lowered
    assert "schema_version_snapshot" in lowered
    for forbidden in (
        "remove-item",
        "rm -rf",
        "chmod ",
        "chown ",
        "docker compose restart",
        "docker compose stop clickhouse",
        "apply-us-m1-schema.ps1",
        "run-us-capacity-pilot.ps1",
        "2023_5.zip",
    ):
        assert forbidden not in lowered


def test_windows_hot_override_remains_explicit_legacy_recovery_only() -> None:
    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    override = OVERRIDE.read_text(encoding="utf-8")
    assert "CLICKHOUSE_HOT_DATA_PATH=E:/MarkOrbitData/hot/clickhouse-cs" in env
    assert "CLICKHOUSE_HOT_DATA_PATH" in override
    assert "legacy E:/MarkOrbitData/hot/clickhouse" in env
