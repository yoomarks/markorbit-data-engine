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
