from pathlib import Path


SCRIPT_DIR = Path("scripts")
RUNBOOK = Path("docs/SINGAPORE_IPOS_OPERATOR_RUNBOOK.md")


def _script(name: str) -> str:
    return (SCRIPT_DIR / name).read_text(encoding="utf-8")


def test_ipos_sg_scripts_resolve_production_state_from_raw_data_path() -> None:
    for name in ("check-ipos-sg.ps1", "run-ipos-sg.ps1"):
        source = _script(name)
        assert '[string]$StateDir = "raw_data\\ipos_sg"' not in source
        assert "$env:RAW_DATA_PATH" in source
        assert 'Join-Path $rawRoot "ipos_sg"' in source
        assert "refusing repo-local fallback" in source


def test_ipos_sg_check_is_read_only_and_does_not_create_state() -> None:
    source = _script("check-ipos-sg.ps1")
    assert "New-Item -ItemType Directory" not in source
    assert "No directory was created." in source


def test_ipos_sg_run_refuses_implicit_production_bootstrap() -> None:
    source = _script("run-ipos-sg.ps1")
    assert "Refusing implicit bootstrap." in source
    assert "Use -StateDir explicitly only for an intentional bootstrap." in source


def test_ipos_sg_runbook_documents_accepted_production_state_root() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "RAW_DATA_PATH" in text
    assert r"F:\MarkOrbitData\raw\ipos_sg" in text


def test_ipos_sg_run_enforces_cn_serving_regression_gate() -> None:
    source = _script("run-ipos-sg.ps1")
    assert "[switch]$ContractOnly" in source
    assert "Get-CnServingSample" in source
    assert "ORDER BY application_number LIMIT 1" in source
    assert "Invoke-CnServingProbe" in source
    assert "/api/health" in source
    assert "/api/cn/cases/$encoded" in source
    assert "Test-CnServingStable" in source
    assert "production_refresh_latest.json" in source
    assert "IPOS_SG_PRODUCTION_REFRESH_ACCEPTANCE_V1" in source
    assert "credential_material_persisted = $false" in source
    assert "recurring_schedule_enabled = $false" in source
    assert "CN serving regression gate: PASS" in source


def test_ipos_sg_run_never_persists_or_prints_api_key() -> None:
    source = _script("run-ipos-sg.ps1")
    assert "DATA_GOV_SG_API_KEY must be set" in source
    assert "--env DATA_GOV_SG_API_KEY" in source
    assert "Write-Host $env:DATA_GOV_SG_API_KEY" not in source
    assert "DATA_GOV_SG_API_KEY =" not in source


def test_ipos_sg_run_requires_clean_exact_main_for_production_refresh() -> None:
    source = _script("run-ipos-sg.ps1")
    assert "[string]$ExpectedMainSha = ''" in source
    assert "Assert-ExactMain" in source
    assert "git rev-parse HEAD" in source
    assert "git rev-parse origin/main" in source
    assert "git status --porcelain=v1" in source
    assert "Working tree must be clean for the controlled Singapore production refresh." in source
    assert "execution_main_sha = $executionMainSha" in source
