from pathlib import Path


def test_us_real_data_acceptance_script_is_read_only_and_worker_guarded() -> None:
    source = Path("scripts/audit-us-real-data.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running -q worker" in source
    assert "Persistent worker is running" in source
    assert '"python", "-m", "app.us.audit_real_data"' in source
    assert "--verify-source-files" in source
    assert "ConvertFrom-Json" in source
    assert "reports" in source

    forbidden = (
        "reset-",
        "run-us.ps1",
        "retry-us.ps1",
        "apply-us-m1-schema.ps1",
        "scan_us_incoming",
        "ingest_us_package",
    )
    for token in forbidden:
        assert token not in source


def test_us_real_data_acceptance_script_fails_closed_on_fail_or_not_ready() -> None:
    source = Path("scripts/audit-us-real-data.ps1").read_text(encoding="utf-8")
    assert '$report.status -eq "FAIL"' in source
    assert '$report.status -eq "NOT_READY"' in source
    assert "hard_fail_reasons" in source
    assert "not_ready_reasons" in source
