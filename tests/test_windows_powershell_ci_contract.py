from pathlib import Path


def test_ci_has_windows_powershell_parser_and_parameter_contract_job() -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    assert "windows-powershell:" in workflow
    assert "runs-on: windows-latest" in workflow
    assert "System.Management.Automation.Language.Parser" in workflow
    assert "Validate critical PowerShell parameter contracts" in workflow
    assert "ExpectedApplicationHistoryParts" in workflow
    assert "assert-storage-headroom.ps1" in workflow
