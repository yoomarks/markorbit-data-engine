from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER = ROOT / "scripts" / "classify-clickhouse-hot-case-contract-native.ps1"


def _text() -> str:
    return CLASSIFIER.read_text(encoding="utf-8")


def test_classifier_uses_native_case_sensitive_flag_not_localized_fsutil_text() -> None:
    text = _text()
    assert "GetFileInformationByName" in text
    assert "FILE_CASE_SENSITIVE_INFORMATION" in text
    assert "FileInformationClass" in text
    assert "($info.Flags -band 0x00000001)" in text
    assert "fsutil.exe file queryCaseSensitiveInfo" not in text
    assert "已启用" not in text
    assert "已禁用" not in text
    assert "disabled\\b" not in text


def test_classifier_freezes_exact_runtime_and_schema_contract() -> None:
    text = _text()
    assert '[string]$ExpectedHotPath = "E:\\MarkOrbitData\\hot\\clickhouse"' in text
    assert '[string]$ExpectedColdPath = "F:\\MarkOrbitData\\cold\\clickhouse"' in text
    assert '[string]$ExpectedLogPath = "E:\\MarkOrbitData\\hot\\clickhouse-logs"' in text
    assert '[string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545"' in text
    assert "Exact-main mismatch" in text
    assert "stop-idle-worker.ps1" in text
    assert "Worker containers must be absent" in text
    assert "ClickHouse must be healthy" in text
    assert ":24\\.8" in text
    assert "markorbit_facts.schema_version FINAL" in text


def test_classifier_reads_five_level_native_chain_and_classifies_without_prior_assumption() -> None:
    text = _text()
    for marker in (
        "label = 'root'",
        "label = 'metadata'",
        "label = 'store'",
        "label = 'store_prefix'",
        "label = 'schema_version_uuid'",
        "'ALL_ENABLED'",
        "'ALL_DISABLED'",
        "'MIXED'",
        "'INCOMPLETE'",
        "case_contract_classification=",
        "case_recovery_required=",
    ):
        assert marker in text
    assert "source_case_chain_all_disabled" not in text
    assert "Frozen source case-sensitivity evidence changed" not in text


def test_classifier_captures_environment_provenance() -> None:
    text = _text()
    assert "GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'Process')" in text
    assert "GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'User')" in text
    assert "GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'Machine')" in text
    assert "local_env_hot_path_match_count=" in text
    assert "actual_hot_mount_source=" in text


def test_classifier_is_strictly_read_only_for_storage_and_us() -> None:
    lowered = _text().lower()
    for forbidden in (
        "docker compose stop",
        "docker stop",
        "docker compose restart",
        "docker restart",
        "setcasesensitiveinfo",
        "rename-item",
        "move-item",
        "remove-item",
        "cp -a ",
        "chmod ",
        "chown ",
        "apply-us-m1-schema.ps1",
        "run-us-capacity-pilot.ps1",
        "replay-us-deterministic.ps1",
        "2023_5.zip",
    ):
        assert forbidden not in lowered
    assert "storage_mutation_performed=false" in lowered
    assert "clickhouse_stop_performed=false" in lowered
    assert "schema_apply_performed=false" in lowered
    assert "corpus_replay_performed=false" in lowered
