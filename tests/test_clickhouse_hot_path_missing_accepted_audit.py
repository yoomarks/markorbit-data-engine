from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "audit-clickhouse-hot-path-regression.ps1"


def test_missing_accepted_path_is_evidence_not_an_early_abort() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    assert "Get-OptionalDirectoryState $AcceptedHotPath 'AcceptedHotPath'" in text
    assert "accepted_hot_path_exists=$($acceptedState.exists)" in text
    assert "ACCEPTED_PATH_MISSING_LEGACY_NAME_ACTIVE_POST_CUTOVER_REGRESSION" in text
    assert "ACCEPTED_PATH_MISSING_UNKNOWN_ACTIVE_POST_CUTOVER_REGRESSION" in text
    assert "CLICKHOUSE_HOT_PATH_REGRESSION_AUDIT_V2_COMPLETE" in text
    assert "Resolve-ExistingDir $AcceptedHotPath 'AcceptedHotPath'" not in text


def test_v2_collects_mount_compose_env_and_case_chain_provenance() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "{{json .Mounts}}" in text
    assert "{{json .Config.Labels}}" in text
    assert "com.docker.compose.project.config_files" in text
    assert "com.docker.compose.project.working_dir" in text
    assert "CLICKHOUSE_HOT_DATA_PATH" in text
    assert "actual_case_chain|label=" in text
    assert "hot_sibling|name=" in text
    assert "schema_version_uuid" in text
    assert "fsutil.exe file queryCaseSensitiveInfo" in text
    assert "safe_to_switch = $false" in lowered


def test_v2_avoids_windows_powershell_51_generic_object_list_binder_trap() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "system.collections.generic.list[object]" not in lowered
    assert "new-object system.collections.generic.list[object]" not in lowered
    assert "return @($rows)" not in lowered
    assert "$rows = @()" in text
    assert "$rows += [pscustomobject][ordered]@{" in text
    assert "[string]::Equals" in text
    assert "AUDIT_RUNTIME_FAILURE" in text
    assert "script_stack_trace=" in text


def test_v2_remains_hot_storage_read_only() -> None:
    lowered = AUDIT.read_text(encoding="utf-8").lower()
    for forbidden in (
        "remove-item",
        "rename-item",
        "move-item",
        "fsutil.exe file setcasesensitiveinfo",
        "rm -rf",
        "chmod ",
        "chown ",
        "docker compose restart",
        "docker compose stop clickhouse",
        "docker compose down",
        "apply-us-m1-schema.ps1",
        "run-us-capacity-pilot.ps1",
        "2023_5.zip",
    ):
        assert forbidden not in lowered
