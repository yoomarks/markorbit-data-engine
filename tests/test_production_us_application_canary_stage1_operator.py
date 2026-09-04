from __future__ import annotations

from pathlib import Path


SCRIPT = Path("scripts/freeze-production-us-application-canary-stage1.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8").lower()


def test_stage1_operator_is_explicitly_read_only_and_exact_main_gated() -> None:
    text = _text()
    assert "expectedmain" in text
    assert "git rev-parse head" in text
    assert "git rev-parse origin/main" in text
    assert "git status --porcelain=v1" in text
    assert "decision=$readydecision" in text
    assert "read_only=true" in text
    assert "package_2_executed=false" in text
    assert "stage2_go_consumed=false" in text


def test_stage1_operator_never_invokes_lifecycle_or_data_mutation_commands() -> None:
    text = _text()
    forbidden = (
        "docker compose run",
        "docker compose up",
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
        "insert into",
        "create table",
    )
    for token in forbidden:
        assert token not in text, token


def test_stage1_operator_refuses_to_start_target_and_requires_keeper() -> None:
    text = _text()
    assert "--list --running --quiet" in text
    assert "target distro is not already running" in text
    assert "tail\\s+-f\\s+/dev/null" in text
    assert "persistent target keeper is not present" in text


def test_stage1_operator_freezes_exact_hot_us_and_warm_identity() -> None:
    text = _text()
    assert "521a7b20-4380-4d6a-8018-2bab78fc2c4b" in text
    assert "274877906944" in text
    assert "/mnt/wsl/markorbit_prod_hot_us/clickhouse-data/" in text
    assert "2ee74d16-f0bd-461b-ab6a-279603e6c570" in text
    assert "842887331840" in text
    assert "hot_us_only" in text
    assert "warm_cn_only" in text


def test_stage1_operator_requires_zero_target_application_tables_and_zero_hot_parts() -> None:
    text = _text()
    assert "first target canary requires all application final tables absent" in text
    assert "system.parts" in text
    assert "hot_us already contains active parts" in text


def test_stage1_operator_materializes_single_line_native_output_before_indexing() -> None:
    text = _text()
    assert "function get-exactsingleline" in text
    assert "$lines = @(invoke-nativecapture" in text
    assert ")[0].trim()" not in text
    assert "([string]$lines[0]).split" in text
