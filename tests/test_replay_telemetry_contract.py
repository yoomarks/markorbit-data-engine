from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_telemetry_helper_is_local_report_only_and_best_effort() -> None:
    text = _script("replay-telemetry.ps1")
    assert "reports\\replay_ledger.jsonl" in text
    assert "reports\\replay_runs" in text
    assert "Add-Content" in text
    assert "Get-FileHash" in text
    assert "System.IO.DriveInfo" in text
    assert "app.replay_telemetry" in text
    assert "source_fact_mutation" in text
    assert "Write-Warning" in text
    assert "INSERT INTO" not in text.upper()
    assert "UPDATE " not in text.upper()
    assert "DELETE FROM" not in text.upper()


def test_cn_full_replay_records_telemetry_without_buffering_replay_stdout() -> None:
    text = _script("replay-cn-full.ps1")
    assert 'replay-telemetry.ps1' in text
    assert 'Start-DataEngineReplayTelemetry' in text
    assert '-Domain "CN"' in text
    assert '-Jurisdiction "CN"' in text
    assert 'Complete-DataEngineReplayTelemetry' in text
    assert '& docker @argsList' in text
    assert '$jsonLines = & docker @argsList' not in text


def test_us_dry_runs_do_not_start_telemetry_and_apply_runs_do() -> None:
    expected = {
        "replay-us-deterministic.ps1": ("US_APPLICATION", "US"),
        "replay-us-assignment-deterministic.ps1": ("US_ASSIGNMENT", "US_ASSIGNMENT"),
        "replay-us-ttab-deterministic.ps1": ("US_TTAB", "US_TTAB"),
    }
    for script, (domain, jurisdiction) in expected.items():
        text = _script(script)
        assert 'replay-telemetry.ps1' in text, script
        assert 'if ($Apply)' in text, script
        assert 'Start-DataEngineReplayTelemetry' in text, script
        assert f'-Domain "{domain}"' in text, script
        assert f'-Jurisdiction "{jurisdiction}"' in text, script
        assert 'Complete-DataEngineReplayTelemetry' in text, script
        assert '-ReportPath $OutputPath' in text, script


def test_reports_are_gitignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "reports/" in gitignore.splitlines()
