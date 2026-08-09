from pathlib import Path


def test_reset_does_not_start_worker():
    script = Path("scripts/reset-m15.ps1").read_text(encoding="utf-8")
    assert "docker compose up -d --build postgres clickhouse api" in script
    assert "docker compose up -d --build\n" not in script


def test_nonempty_fixture_exists_and_uses_two_publishes():
    source = Path("app/cn/validate_fixture.py").read_text(encoding="utf-8")
    assert source.count("_publish(") >= 2
    assert "Gamma Fixture Ltd" in source
    assert "OWNER_RELATION_SUPERSEDED_OBSERVED" in source
    assert "G99000001A" in source
    assert "MADRID_DESIGNATION_CN" in source


def test_real_run_refuses_worker_race():
    script = Path("scripts/run-cn.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running --services worker" in script
    assert 'throw "persistent worker is running' in script


def test_validation_order_is_explicit():
    contract = Path("scripts/validate-cn-contract.ps1").read_text(encoding="utf-8")
    fixture = Path("scripts/validate-cn-fixture.ps1").read_text(encoding="utf-8")
    assert "do NOT run a real ZIP yet" in contract
    assert "python -m app.cn.validate_fixture" in fixture


def test_retry_uses_both_fast_gates_and_refuses_worker_race():
    script = Path("scripts/retry-cn.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running --services worker" in script
    assert "python -m app.cn.validate_contract" in script
    assert "python -m app.cn.validate_fixture" in script
    assert script.index("validate_contract") < script.index("validate_fixture") < script.index("/api/jobs/cn/retry")
