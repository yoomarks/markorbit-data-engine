from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reconcile-orphaned-admin-job-no-worker.ps1"


def test_orphan_admin_reconciliation_is_exact_cas_and_fail_closed() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "[string]$RunId" in text
    assert "[string]$ExpectedMainSha" in text
    assert "[Int64]$ExpectedStartedAtEpochMicros" in text
    assert 'ExpectedJobType = "CN_ADMIN_CONTINUE"' in text

    assert "EXACT_MAIN_ORPHAN_RECONCILIATION_OK" in text
    assert "ZERO_WORKER_CONTAINER_GATE_OK" in text
    assert "NO_LIVE_WORKLOAD_GATE_OK" in text
    assert "worker_container_count_all_states_final=" in text

    assert "global_unfinished_jobs=" in text
    assert "global_processing_packages=" in text
    assert "exact_target_matches=" in text
    assert "postgres_non_idle_sessions=" in text
    assert "clickhouse_active_queries=" in text

    assert "pg_advisory_xact_lock(hashtext('markorbit:admin-domain-task-queue'))" in text
    assert "UPDATE control.job_run" in text
    assert "SET status = 'INTERRUPTED'" in text
    assert "finished_at = now()" in text
    assert "ORPHANED_ADMIN_NO_WORKER" in text
    assert "lifecycle_reconciliation" in text

    assert "WHERE run_id = '__RUN_ID__'::uuid" in text
    assert "AND job_type = '__JOB_TYPE__'" in text
    assert "AND trigger_type = 'ADMIN_UI'" in text
    assert "AND status = 'RUNNING'" in text
    assert "AND finished_at IS NULL" in text
    assert "payload->>'task_kind' = 'DOMAIN_CONTROL'" in text
    assert "payload->>'domain' = 'CN'" in text
    assert "payload->>'action' = 'CONTINUE'" in text
    assert "coalesce(payload->>'stop_requested', 'false') = 'false'" in text
    assert "round(extract(epoch FROM started_at) * 1000000)::bigint = __EXPECTED_EPOCH__" in text

    assert "GET DIAGNOSTICS affected = ROW_COUNT" in text
    assert "IF affected <> 1" in text
    assert "RAISE EXCEPTION 'Orphan Admin CAS mismatch" in text

    assert "global_unfinished_jobs_after=" in text
    assert "reconciliation_performed=True" in text
    assert "reconciliation_status=INTERRUPTED" in text
    assert "worker_start_performed=False" in text
    assert "worker_stop_performed=False" in text
    assert "schema_apply_performed=False" in text
    assert "corpus_replay_performed=False" in text
    assert "permission_repair_performed=False" in text
    assert "ORPHANED_ADMIN_JOB_CAS_RECONCILIATION_COMPLETE" in text

    assert "docker compose start worker" not in lowered
    assert "docker compose stop worker" not in lowered
    assert "docker compose restart worker" not in lowered
    assert "docker compose down" not in lowered
    assert "delete from control.job_run" not in lowered
    assert "insert into control.job_run" not in lowered
    assert "apply-us-m1-schema.ps1" not in lowered
    assert "replay-us-deterministic.ps1" not in lowered
    assert "run-us-capacity-pilot.ps1" not in lowered
    assert "2023_5.zip" not in lowered
    assert "chmod " not in lowered
    assert "chown " not in lowered
    assert re.search(r"(?i)(?<![A-Za-z0-9_-])-All(?![A-Za-z0-9_-])", text) is None
