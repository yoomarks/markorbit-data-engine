from pathlib import Path


def test_four_domain_acceptance_script_consumes_frozen_reports_read_only() -> None:
    source = Path("scripts/audit-four-domain-acceptance.ps1").read_text(encoding="utf-8")
    assert "ExpectedApplicationHistoryParts = 91" in source
    assert "ExpectedApplicationDailyThrough" in source
    assert "Get-FileHash" in source
    assert "ConvertFrom-Json" in source
    assert "ConvertTo-Json" in source
    assert "python -m app.four_domain_acceptance --stdin" in source
    assert "docker compose ps --status running -q worker" in source
    assert "Persistent worker is running" in source
    assert "Set-Content -Encoding UTF8" in source
    assert "reports" in source

    forbidden = (
        "reset-m16.ps1",
        "replay-cn-full.ps1",
        "replay-us-deterministic.ps1",
        "replay-us-assignment-deterministic.ps1",
        "replay-us-ttab-deterministic.ps1",
        "run-us.ps1",
        "start-worker.ps1",
    )
    for token in forbidden:
        assert token not in source


def test_four_domain_acceptance_script_fails_closed() -> None:
    source = Path("scripts/audit-four-domain-acceptance.ps1").read_text(encoding="utf-8")
    assert '$report.status -eq "FAIL"' in source
    assert "hard_fail_reasons" in source
    assert "$exitCode -ne 0" in source
