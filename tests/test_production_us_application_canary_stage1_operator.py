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


def test_stage1_operator_refuses_to_start_target_and_requires_exact_keeper() -> None:
    text = _text()
    assert "--list --running --quiet" in text
    assert "target distro is not already running" in text
    assert "$keeperpid = 27700" in text
    assert "processid=$keeperpid" in text
    assert "tail\\s+-f\\s+/dev/null" in text
    assert "accepted target keeper pid" in text


def test_stage1_operator_matches_actual_clickhouse_server_command_shape() -> None:
    text = _text()
    assert "[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml" in text
    assert "[c]lickhouse-server" not in text


def test_stage1_operator_freezes_exact_target_config_and_runtime_version() -> None:
    text = _text()
    assert "24.8.14.39" in text
    assert "c7240b6c05a96dff2dc4c9e5a801cd524065bd101b5d006f2e8610b63ca56a59" in text
    assert "16b281607c47f9ee1f1bd8e3d09c4fc556320e833f17d05b597dec78aa2eb233" in text
    assert "/opt/markorbit-clickhouse-production/config.xml" in text
    assert "/opt/markorbit-clickhouse-production/users.xml" in text


def test_stage1_operator_freezes_exact_hot_us_and_warm_identity() -> None:
    text = _text()
    assert "521a7b20-4380-4d6a-8018-2bab78fc2c4b" in text
    assert "274877906944" in text
    assert "/mnt/wsl/markorbit_prod_hot_us/clickhouse-data/" in text
    assert "2ee74d16-f0bd-461b-ab6a-279603e6c570" in text
    assert "842887331840" in text
    assert "/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/" in text
    assert "hot_us_only" in text
    assert "warm_cn_only" in text


def test_stage1_operator_requires_zero_target_application_tables_and_zero_custom_disk_parts() -> None:
    text = _text()
    assert "first target canary requires all application final tables absent" in text
    assert "system.parts" in text
    assert "hot_us already contains active parts" in text
    assert "warm_cn unexpectedly contains active parts" in text


def test_stage1_operator_reproves_source_config_and_capacity_floor() -> None:
    text = _text()
    assert "baa0b2ff85869e066fa1f27087339c6d0648c87e64cae6ce49915bf345ab9b1f" in text
    assert "/etc/clickhouse-server/config.xml" in text
    assert "deviceid='d:'" in text
    assert "0.30" in text
    assert "d_30pct_floor_satisfied" in text


def test_stage1_operator_materializes_single_line_native_output_before_indexing() -> None:
    text = _text()
    assert "function get-exactsingleline" in text
    assert "$lines = @(invoke-nativecapture" in text
    assert ")[0].trim()" not in text
    assert "([string]$lines[0]).split" in text
