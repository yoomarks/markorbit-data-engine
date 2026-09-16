from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preflight-us-secondary-family-target-to-source-connectivity.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "us-secondary-family-connectivity-preflight-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_binds_exact_accepted_schema_apply_artifacts() -> None:
    source = text()
    for marker in (
        "dd8c5192f1be4dff3177c06795fd0b45a0b74eea12a3cf400f2934ec3a03755c",
        "8083c12a53b1b0f6fab2f43c73d95c2a569c42176cb081db6da6b467cf6538ad",
        "d9dc18fe66381d101bd5b94d2b155da1ed3f4c45102bd06e36e75aab30094620",
        "US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_SUCCESS",
        "TARGET_TO_SOURCE_CONNECTIVITY_PREFLIGHT",
    ):
        assert marker in source


def test_runtime_requires_exact_clean_main() -> None:
    source = text()
    assert "git branch --show-current" in source
    assert "git rev-parse origin/main" in source
    assert "Working tree must be clean" in source


def test_gateway_is_derived_from_target_wsl_default_route() -> None:
    source = text()
    fn = source.split("function Resolve-TargetGateway", 1)[1].split("function Assert-SourceDockerReady", 1)[0]
    assert "ip route show default" in fn
    assert "default\\s+via" in fn
    assert "return $gateway" in fn


def test_source_native_port_must_remain_host_published() -> None:
    source = text()
    fn = source.split("function Assert-SourceDockerReady", 1)[1].split("function Invoke-TargetWslSourceSql", 1)[0]
    assert "docker' @('compose','port','clickhouse'" in fn
    assert "9000" in fn
    assert "Source native 9000 is not published" in fn


def test_authenticated_probe_keeps_credentials_out_of_argv_and_sql_on_stdin() -> None:
    source = text()
    fn = source.split("function Invoke-TargetWslSourceSql", 1)[1].split("function Get-RemoteSourceIdentity", 1)[0]
    assert "$env:MO_SRC_CH_USER=$User" in fn
    assert "$env:MO_SRC_CH_PASSWORD=$Password" in fn
    assert "WSLENV" in fn
    assert "RedirectStandardInput=$true" in fn
    assert "$process.StandardInput.Write($Sql)" in fn
    assert "--password `\"`$MO_SRC_CH_PASSWORD`\"" in fn
    assert "--password $Password" not in fn


def test_target_server_path_uses_expected_unauthenticated_auth_failure() -> None:
    source = text()
    fn = source.split("function Assert-TargetServerCanReachSource", 1)[1].split("function Invoke-ContractFixture", 1)[0]
    assert "remote('$Gateway`:$($script:SourceNativePort)'" in fn
    assert "AUTHENTICATION_FAILED" in fn
    assert "credential_material_sent=$false" in fn


def test_source_identity_is_compared_local_vs_authenticated_target_view() -> None:
    source = text()
    for marker in (
        "Get-SourceIdentityLocal",
        "Get-RemoteSourceIdentity",
        "Target-WSL authenticated source identity does not match local authoritative source",
        "source_identity_match=$true",
    ):
        assert marker in source


def test_target_must_remain_eight_empty_hot_us_only_tables() -> None:
    source = text()
    fn = source.split("function Get-TargetEmptySchemaState", 1)[1].split("function Assert-TargetServerCanReachSource", 1)[0]
    assert "Expected 8 target schemas" in fn
    assert "storage_policy" in fn
    assert "$script:TargetPolicy" in fn
    assert "active_parts" in fn
    assert "Target schemas are not empty" in fn


def test_receipt_is_secret_free_and_copy_stays_unauthorized() -> None:
    source = text()
    for marker in (
        "credential_values_persisted=False",
        "values_in_receipt=$false",
        "values_printed=$false",
        "copy_authorized=$false",
        "serving_cutover_authorized=$false",
        "source_cleanup_authorized=$false",
    ):
        assert marker in source
    assert "Write-Host $Password" not in source
    receipt_block = source.split("$receipt=[ordered]@{", 1)[1].split("Write-JsonAtomic $receipt", 1)[0]
    assert "$password" not in receipt_block.lower()
    probe_fn = source.split("function Invoke-TargetWslSourceSql", 1)[1].split("function Get-RemoteSourceIdentity", 1)[0]
    assert "--password $Password" not in probe_fn


def test_success_stops_at_assignment_copy_review() -> None:
    source = text()
    assert "US_SECONDARY_FAMILY_CONNECTIVITY_PREFLIGHT_READY" in source
    assert "BOUNDED_ASSIGNMENT_TABLE_COPY_REVIEW" in source
    assert "copy_authorized=False" in source


def test_operator_contains_no_copy_or_schema_mutation_executor() -> None:
    source = text()
    for forbidden in (
        "INSERT INTO markorbit_facts",
        "CREATE TABLE markorbit_facts",
        "DROP TABLE markorbit_facts",
        "ALTER TABLE markorbit_facts",
        "TRUNCATE TABLE markorbit_facts",
        "remoteSecure(",
    ):
        assert forbidden not in source


def test_contract_only_runs_under_windows_powershell_51() -> None:
    if shutil.which("powershell.exe") is None:
        pytest.skip("Windows PowerShell 5.1 is validated by the dedicated windows-latest job")
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(SCRIPT),
        "-ExpectedMainSha", "8fc783eb2b60b2341592e80953ec55007d1ad00b",
        "-ContractOnly",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "US_SECONDARY_FAMILY_CONNECTIVITY_PREFLIGHT_CONTRACT_PASS" in output
    assert "copy_authorized=False" in output


def test_workflow_runs_windows_ps51_and_python_contracts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "windows-latest",
        "powershell.exe -NoProfile",
        "-ContractOnly",
        "US_SECONDARY_FAMILY_CONNECTIVITY_PREFLIGHT_CONTRACT_PASS",
        "pytest",
        "test_us_secondary_family_connectivity_preflight_contract.py",
        "concurrency:",
    ):
        assert marker in workflow
