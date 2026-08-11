from pathlib import Path


def test_odp_metadata_preflight_is_read_only_and_worker_guarded() -> None:
    source = Path("scripts/preflight-uspto-odp-bulk-metadata.ps1").read_text(encoding="utf-8")
    assert '[ValidateSet("assignment", "ttab")]' in source
    assert "ExpectedFileName" in source
    assert "ConvertFrom-Json" in source
    assert "ConvertTo-Json" in source
    assert "python -m app.uspto_odp_bulk_metadata --stdin" in source
    assert "docker compose ps --status running -q worker" in source
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


def test_odp_metadata_preflight_fails_closed() -> None:
    source = Path("scripts/preflight-uspto-odp-bulk-metadata.ps1").read_text(encoding="utf-8")
    assert "$exitCode -ne 0" in source
    assert "-not $report.safe" in source
    assert "USPTO ODP metadata preflight not ready" in source
