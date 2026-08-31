from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "scripts" / "assert-clickhouse-active-hot-storage-contract.ps1"
AUDIT = ROOT / "scripts" / "audit-clickhouse-hot-path-regression.ps1"
HOT_V2 = ROOT / "scripts" / "diagnose-clickhouse-active-hot-permissions-v2.ps1"
ENV_EXAMPLE = ROOT / ".env.example"
OVERRIDE = ROOT / "docker-compose.hot-cold-storage.yml"


def test_active_hot_contract_freezes_accepted_case_sensitive_root() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    assert 'E:\\MarkOrbitData\\hot\\clickhouse-cs' in text
    assert 'E:\\MarkOrbitData\\hot\\clickhouse"' in text
    assert "REJECTED_LEGACY_CASE_INSENSITIVE_HOT_PATH" in text
    assert "ACTIVE_HOT_SOURCE_NOT_ACCEPTED_CLICKHOUSE_CS" in text
    assert "ACTIVE_HOT_CASE_SENSITIVITY_NOT_ENABLED" in text
    assert "fsutil.exe file queryCaseSensitiveInfo" in text
    assert "{{json .Mounts}}" in text
    assert "/var/lib/clickhouse" in text
    assert "CLICKHOUSE_ACTIVE_HOT_STORAGE_CONTRACT_PASS" in text


def test_hot_v2_checks_storage_contract_before_permission_diagnostic() -> None:
    text = HOT_V2.read_text(encoding="utf-8")
    contract = text.index("assert-clickhouse-active-hot-storage-contract.ps1")
    permission = text.index("diagnose-clickhouse-active-hot-permissions.ps1")
    assert contract < permission
    assert "ACTIVE_HOT_STORAGE_CONTRACT_OK" in text


def test_read_only_regression_audit_never_authorizes_switch() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "REJECTED_LEGACY_HOT_PATH_ACTIVE_OFFLINE_PARITY_REQUIRED" in text
    assert "ACCEPTED_CLICKHOUSE_CS_ALREADY_ACTIVE" in text
    assert "safe_to_switch = $false" in lowered
    assert "filesystem_mutation_performed = $false" in lowered
    assert "corpus_replay_performed = $false" in lowered
    assert "get-filehash" in lowered
    assert "get-storeuuidindex" in lowered
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


def test_examples_no_longer_point_hot_root_at_rejected_legacy_path() -> None:
    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    override = OVERRIDE.read_text(encoding="utf-8")
    assert "CLICKHOUSE_HOT_DATA_PATH=E:/MarkOrbitData/hot/clickhouse-cs" in env
    assert "CLICKHOUSE_HOT_DATA_PATH   e.g. E:/MarkOrbitData/hot/clickhouse-cs" in override
    assert "legacy E:/MarkOrbitData/hot/clickhouse" in env
    assert "legacy\n# E:/MarkOrbitData/hot/clickhouse root was rejected" in override
