from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose-clickhouse-native-merge-tree-rename.ps1"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _assert_no_standalone_all(text: str) -> None:
    assert re.search(r"(?i)(?<![A-Za-z0-9_-])-All(?![A-Za-z0-9_-])", text) is None


def test_native_merge_tree_probe_is_exact_main_idle_and_zero_worker() -> None:
    text = _text()

    assert "[string]$ExpectedMainSha" in text
    assert "git status --porcelain" in text
    assert "git fetch origin main" in text
    assert "origin/main" in text
    assert "EXACT_MAIN_NATIVE_MERGETREE_RENAME_OK" in text
    assert "stop-idle-worker.ps1" in text
    assert "docker compose ps --status running -q worker" in text
    assert "docker compose ps -a -q worker" in text
    assert "ZERO_WORKER_CONTAINERS_OK" in text
    assert "clickhouse_docker_health=healthy" in text
    assert "system.processes" in text
    assert "system.mutations" in text


def test_native_merge_tree_probe_exercises_real_clickhouse_part_commit_only_in_disposable_database() -> None:
    text = _text()

    assert 'Get-Date -Format "yyyyMMdd_HHmmssfff"' in text
    assert "^markorbit_native_rename_probe_[0-9]{8}_[0-9]{9}$" in text
    assert 'CREATE DATABASE $probeDatabase' in text
    assert "CREATE TABLE $probeDatabase.$probeTable" in text
    assert "ENGINE = MergeTree ORDER BY probe_id" in text
    assert "INSERT INTO $probeDatabase.$probeTable VALUES (1, 'native-part-rename-probe')" in text
    assert "system.parts" in text
    assert "tmp_insert_*" in text
    assert "probe_insert_succeeded=" in text
    assert "probe_tmp_insert_count=" in text
    assert "probe_row_count=" in text
    assert "DROP DATABASE IF EXISTS $probeDatabase SYNC" in text
    assert "probe_cleanup_succeeded=" in text


def test_native_merge_tree_probe_preserves_business_schema_state_and_classifies_scope() -> None:
    text = _text()

    assert "schema_version_snapshot_before=" in text
    assert "schema_version_snapshot_after=" in text
    assert "schema_version_snapshot_preserved=" in text
    assert "schema_version_tmp_insert_count_before=" in text
    assert "schema_version_tmp_insert_count_after=" in text
    assert "NATIVE_MERGETREE_RENAME_PASS_SCHEMA_VERSION_SPECIFIC_SUSPECT" in text
    assert "NATIVE_MERGETREE_RENAME_FAIL_ACTIVE_HOT_BIND_SUSPECT" in text
    assert "CLICKHOUSE_NATIVE_MERGETREE_RENAME_DIAGNOSTIC_COMPLETE" in text


def test_native_merge_tree_probe_has_no_business_repair_or_rollout_escape_hatches() -> None:
    text = _text()
    lowered = text.lower()

    assert "insert into markorbit_facts.schema_version" not in lowered
    assert "update markorbit_facts.schema_version" not in lowered
    assert "delete from markorbit_facts.schema_version" not in lowered
    assert "alter table markorbit_facts" not in lowered
    assert "drop table markorbit_facts" not in lowered
    assert "truncate table markorbit_facts" not in lowered
    assert "docker compose start worker" not in lowered
    assert "docker compose restart worker" not in lowered
    assert "docker compose stop worker" not in lowered
    assert "docker compose down" not in lowered
    assert "apply-us-m1-schema.ps1" not in lowered
    assert "replay-us-deterministic.ps1" not in lowered
    assert "run-us-capacity-pilot.ps1" not in lowered
    assert "2023_5.zip" not in lowered
    assert "chmod " not in lowered
    assert "chown " not in lowered
    assert "rm -rf" not in lowered
    _assert_no_standalone_all(text)

    assert "manual_filesystem_cleanup_performed=False" in text
    assert "permission_repair_performed=False" in text
    assert "schema_apply_performed=False" in text
    assert "corpus_replay_performed=False" in text
    assert "worker_start_performed=False" in text
    assert "worker_stop_performed=False" in text
