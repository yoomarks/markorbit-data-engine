from pathlib import Path


SCRIPT = Path("scripts/run-production-us-application-canary-stage2.ps1")
MODULE = Path("app/us/target_canary_stage2.py")


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8").lower()


def _module() -> str:
    return MODULE.read_text(encoding="utf-8").lower()


def test_stage2_operator_pins_exact_package2_authority_and_execution_main() -> None:
    text = _script()
    assert "[string]$expectedmain" in text
    assert "go #526 stage 2 bounded us application canary" in text
    assert "assert-exactexecutionmain -phase 'entry' -fetchorigin $true" in text
    assert "assert-exactexecutionmain -phase 'immediate-pre-mutation' -fetchorigin $true" in text
    assert "git status --porcelain=v1" in text
    assert "git fetch origin main" in text
    assert "d92f430913ef0684c386c2d7bcb767aa2d3284f8" in text
    assert "apc18840407-20251231-02.zip" in text
    assert "5997232" in text
    assert "96555bf13b6e8c2f2ede3433c88e4c600b7115ef3e4d7d22f28c8263cada60c7" in text
    assert "aec9c8b5-f680-5881-94fb-71a1f8e44152" in text
    assert "ff801dea29e5f4b146e5e7ca24507abf4d7d498f977af64e1bc2e14267f63795" in text


def test_stage2_operator_reuses_stable_journal_and_stops_after_package2() -> None:
    text = _script()
    assert "production_us_application_canary_stage2_state\\package2_" in text
    assert "canary_journal.json" in text
    assert "package_2_executed=true" in text
    assert "package_3_executed=false" in text
    assert "full_corpus_executed=false" in text
    assert "automatic_next_package=false" in text
    assert "blind_retry_permitted = $false" in text
    assert "bounded_us_application_canary_stage2_package2_accepted" in text


def test_stage2_operator_never_changes_runtime_lifecycle_or_runs_destructive_sql() -> None:
    text = _script()
    forbidden = (
        "docker compose",
        "docker exec",
        "docker restart",
        "docker stop",
        "docker start",
        "docker rm",
        "docker volume rm",
        "wsl.exe --mount",
        "wsl.exe --unmount",
        "wsl.exe --shutdown",
        "wsl.exe --terminate",
        "wsl.exe --unregister",
        "alter table",
        "delete where",
        "truncate table",
        "optimize table",
        "move partition",
        "detach table",
        "attach table",
        "drop table",
    )
    for token in forbidden:
        assert token not in text, token


def test_stage2_operator_rechecks_target_keeper_storage_and_capacity() -> None:
    text = _script()
    assert "$keeperpid = 27700" in text
    assert "tail\\s+-f\\s+/dev/null" in text
    assert "[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml" in text
    assert "24.8.14.39" in text
    assert "c7240b6c05a96dff2dc4c9e5a801cd524065bd101b5d006f2e8610b63ca56a59" in text
    assert "16b281607c47f9ee1f1bd8e3d09c4fc556320e833f17d05b597dec78aa2eb233" in text
    assert "521a7b20-4380-4d6a-8018-2bab78fc2c4b" in text
    assert "2ee74d16-f0bd-461b-ab6a-279603e6c570" in text
    assert "warm_cn unexpectedly contains active parts" in text
    assert "application target parts escaped hot_us" in text
    assert "deviceid='d:'" in text
    assert "0.30" in text


def test_stage2_python_orchestrator_uses_staging_journal_and_no_general_replay() -> None:
    text = _module()
    assert "stage_package_rows" in text
    assert "initialize_canary_journal" in text
    assert "mark_stage_started" in text
    assert "mark_stage_complete" in text
    assert "commit_staged_tables" in text
    assert "explicit read-only stage reconciliation is required" in text
    assert "registry_write_performed" in text
    assert "automatic_next_package" in text
    assert "register_us_package" not in text
    assert "execute_replay" not in text
    assert "ingest_us_package" not in text
