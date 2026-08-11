from pathlib import Path


def test_cn_acceptance_script_is_read_only_worker_guarded_and_persists_report() -> None:
    source = Path("scripts/audit-m16-acceptance.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running -q worker" in source
    assert "Persistent worker is running" in source
    assert "docker compose run --build --rm --no-deps -T worker python -m app.cn.audit_acceptance" in source
    assert "ConvertFrom-Json" in source
    assert "Set-Content -Encoding UTF8" in source
    assert "reports" in source
    assert "package_registry" in source

    forbidden = (
        "reset-m16.ps1",
        "replay-cn-full.ps1",
        "retry-cn.ps1",
        "run-cn.ps1",
        "start-worker.ps1",
    )
    for token in forbidden:
        assert token not in source


def test_cn_acceptance_script_fails_closed_on_fail_or_not_ready() -> None:
    source = Path("scripts/audit-m16-acceptance.ps1").read_text(encoding="utf-8")
    assert '$report.status -eq "FAIL"' in source
    assert '$report.status -eq "NOT_READY"' in source
    assert "hard_fail_reasons" in source
    assert "not_ready_reasons" in source


def test_cn_acceptance_python_entrypoint_propagates_failed_status() -> None:
    source = Path("app/cn/audit_acceptance.py").read_text(encoding="utf-8")
    assert 'return 0 if result.get("status") in {"PASS", "PASS_WITH_WARNINGS"} else 2' in source
    assert "raise SystemExit(main())" in source
