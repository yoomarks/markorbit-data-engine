from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose-orphaned-admin-job-no-worker.ps1"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_no_worker_orphan_diagnostic_is_exact_and_read_only() -> None:
    text = _text()

    assert "[string]$RunId" in text
    assert "[string]$ExpectedMainSha" in text
    assert 'ExpectedJobType = "CN_ADMIN_CONTINUE"' in text
    assert "EXACT_MAIN_NO_WORKER_READ_ONLY_OK" in text
    assert "docker compose ps --status running -q worker" in text
    assert "docker compose ps -a -q worker" in text
    assert "worker_running_count=" in text
    assert "worker_container_count_all_states=" in text
    assert "NO_WORKER_CONTAINERS" in text
    assert "WHERE run_id = '$canonicalRunId'::uuid" in text
    assert "status=$jobStatus" in text
    assert "global_processing_packages=" in text
    assert "orphan_candidate_preconditions_met=" in text
    assert "pg_stat_activity" in text
    assert "system.processes" in text
    assert "system.query_log" in text
    assert "ownership_requires_review=True" in text
    assert "reconciliation_performed=False" in text
    assert "worker_start_performed=False" in text
    assert "worker_stop_performed=False" in text
    assert "schema_apply_performed=False" in text
    assert "corpus_replay_performed=False" in text
    assert "permission_repair_performed=False" in text
    assert "ORPHANED_ADMIN_JOB_NO_WORKER_DIAGNOSTIC_COMPLETE" in text

    lowered = text.lower()
    assert "datetimeoffset" not in lowered
    assert "parse(" not in lowered
    assert "update control.job_run" not in lowered
    assert "delete from control.job_run" not in lowered
    assert "insert into control.job_run" not in lowered
    assert "docker compose start" not in lowered
    assert "docker compose stop" not in lowered
    assert "docker compose restart" not in lowered
    assert "docker compose down" not in lowered
    assert "apply-us-m1-schema.ps1" not in lowered
    assert "replay-us-deterministic.ps1" not in lowered
    assert "run-us-capacity-pilot.ps1" not in lowered
    assert "2023_5.zip" not in lowered
    assert re.search(r"(?i)(?<![A-Za-z0-9_-])-All(?![A-Za-z0-9_-])", text) is None
    assert "chmod " not in lowered
    assert "chown " not in lowered
