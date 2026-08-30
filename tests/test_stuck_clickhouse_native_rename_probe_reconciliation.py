from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reconcile-stuck-clickhouse-native-rename-probe.ps1"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_stuck_probe_reconciliation_is_exact_incident_scoped() -> None:
    text = _text()

    assert "markorbit_native_rename_probe_20260831_035202137" in text
    assert "[string]$ExpectedMainSha" in text
    assert "git fetch origin main" in text
    assert "EXACT_MAIN_STUCK_NATIVE_PROBE_OK" in text
    assert "stop-idle-worker.ps1" in text
    assert "worker_container_count_all_states=" in text
    assert "clickhouse_docker_health=healthy" in text
    assert "5|5|2026-08-10 12:58:08.545" in text
    assert "position(query, '$probeNeedle') > 0" in text
    assert "exact_probe_active_query_count=" in text
    assert "KILL QUERY WHERE query_id = '$queryId' ASYNC" in text
    assert "exact_probe_query_remaining_after_kill=" in text
    assert "DROP DATABASE IF EXISTS $probeDatabase SYNC" in text
    assert "probe_database_exists_after_cleanup=0" not in text
    assert "STUCK_NATIVE_MERGETREE_PROBE_RECONCILIATION_PASS" in text


def test_stuck_probe_reconciliation_has_no_business_or_filesystem_mutation() -> None:
    text = _text()
    lowered = text.lower()

    assert "insert into markorbit_facts" not in lowered
    assert "update markorbit_facts" not in lowered
    assert "delete from markorbit_facts" not in lowered
    assert "alter table markorbit_facts" not in lowered
    assert "drop table markorbit_facts" not in lowered
    assert "truncate table markorbit_facts" not in lowered
    assert "docker compose start worker" not in lowered
    assert "docker compose restart worker" not in lowered
    assert "docker compose stop worker" not in lowered
    assert "docker compose down" not in lowered
    assert "chmod " not in lowered
    assert "chown " not in lowered
    assert "rm -rf" not in lowered
    assert "2023_5.zip" not in lowered
    assert re.search(r"(?i)(?<![A-Za-z0-9_-])-All(?![A-Za-z0-9_-])", text) is None

    assert "query_kill_scope=EXACT_DISPOSABLE_PROBE_ONLY" in text
    assert "normal_sql_cleanup_only=True" in text
    assert "manual_filesystem_cleanup_performed=False" in text
    assert "permission_repair_performed=False" in text
    assert "schema_apply_performed=False" in text
    assert "corpus_replay_performed=False" in text
    assert "worker_start_performed=False" in text
    assert "worker_stop_performed=False" in text
