from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
IDLE_WORKER = ROOT / "scripts" / "stop-idle-worker.ps1"
HOT_DIAGNOSTIC = ROOT / "scripts" / "diagnose-clickhouse-active-hot-permissions.ps1"
ADMIN_JOB_DIAGNOSTIC = ROOT / "scripts" / "diagnose-admin-job-runtime.ps1"
PREPARE = ROOT / "scripts" / "prepare-us-capacity-pilot-target-host.ps1"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_no_all_switch(text: str) -> None:
    assert re.search(r"(?i)(?<![A-Za-z0-9_-])-All(?![A-Za-z0-9_-])", text) is None


def test_idle_worker_stop_is_global_idle_and_explicit() -> None:
    text = _text(IDLE_WORKER)

    assert "finished_at IS NULL" in text
    assert "status = 'PROCESSING'" in text
    assert "[switch]$StopIdleWorker" in text
    assert "docker compose stop worker" in text
    assert "GLOBAL_DATA_ENGINE_IDLE_OK" in text
    assert "IDLE_WORKER_STOP_GATE_PASS" in text

    assert 'printenv $Name' in text
    assert '"POSTGRES_USER"' in text
    assert '"POSTGRES_DB"' in text
    assert '$statusArgs = @(' in text
    assert '$detailArgs = @(' in text
    assert '& docker @statusArgs' in text
    assert '& docker @detailArgs' in text

    assert "coalesce(job_type, '')" in text
    assert "coalesce(trigger_type, '')" in text
    assert "run_id::text" in text
    assert "coalesce(package_kind, '')" in text
    assert "package_id::text" in text
    assert "coalesce(file_name, '')" in text
    assert "ORDER BY package_sequence" in text

    lowered = text.lower()
    assert "coalesce(domain" not in lowered
    assert "source_kind" not in lowered
    assert "coalesce(id::text" not in lowered
    assert "order by updated_at" not in lowered
    assert "sh -lc" not in lowered
    assert "docker compose start worker" not in lowered
    assert "docker compose restart worker" not in lowered
    assert "docker compose down" not in lowered


def test_hot_permission_diagnostic_is_evidence_only() -> None:
    text = _text(HOT_DIAGNOSTIC)

    assert "markorbit_facts.schema_version" in text
    assert "arrayStringConcat(data_paths" in text
    assert "tmp_insert_*" in text
    assert "--user $serverIdentity" in text
    assert ".markorbit-permission-probe-" in text
    assert 'repair_attempted = $false' in text
    assert 'safe_to_apply_schema = $false' in text
    assert "ACTIVE_HOT_PERMISSION_DIAGNOSTIC_COMPLETE" in text
    assert 'Get-ContainerStat "/var/lib/clickhouse"' in text
    assert "LastIndexOf('/')" in text

    lowered = text.lower()
    assert "chmod " not in lowered
    assert "chown " not in lowered
    assert "alter table" not in lowered
    assert "drop table" not in lowered
    assert "truncate table" not in lowered
    assert "rm -rf '/var/lib/clickhouse/store" not in lowered
    assert 'rm -rf "/var/lib/clickhouse/store' not in lowered


def test_admin_job_runtime_diagnostic_is_read_only_and_exact_run_scoped() -> None:
    text = _text(ADMIN_JOB_DIAGNOSTIC)

    assert "[string]$RunId" in text
    assert "[string]$ExpectedMainSha" in text
    assert 'ExpectedJobType = "CN_ADMIN_CONTINUE"' in text
    assert "WHERE run_id = '$canonicalRunId'::uuid" in text
    assert "payload::text" in text
    assert "metrics::text" in text
    assert "error_message" in text
    assert "global_processing_packages=" in text
    assert "worker_running_count=" in text
    assert "worker_container_count_all_states=" in text
    assert "worker_ownership_state=" in text
    assert "NO_RUNNING_WORKER" in text
    assert "SINGLE_RUNNING_WORKER" in text
    assert "MULTIPLE_RUNNING_WORKERS" in text
    assert "docker compose ps -a -q worker" in text
    assert "started_after_job_claim" in text
    assert "finished_after_job_claim" in text
    assert "worker_runtime_matches_exact_checkout" in text
    assert "NOT_APPLICABLE_NO_RUNNING_WORKER" in text
    assert "docker exec $workerId python" in text
    assert "app/admin_domain_tasks.py" in text
    assert "app/cn/final_checkpoint.py" in text
    assert "system.processes" in text
    assert "system.query_log" in text
    assert "pg_stat_activity" in text
    assert "ownership_requires_review=True" in text
    assert "reconciliation_performed=False" in text
    assert "worker_stop_performed=False" in text
    assert "schema_apply_performed=False" in text
    assert "corpus_replay_performed=False" in text
    assert "permission_repair_performed=False" in text
    assert "ADMIN_JOB_RUNTIME_DIAGNOSTIC_COMPLETE" in text
    assert "Exactly one persistent worker must be running" not in text

    lowered = text.lower()
    assert "update control.job_run" not in lowered
    assert "delete from control.job_run" not in lowered
    assert "insert into control.job_run" not in lowered
    assert "docker compose stop" not in lowered
    assert "docker compose restart" not in lowered
    assert "docker compose down" not in lowered
    assert "apply-us-m1-schema.ps1" not in lowered
    assert "replay-us-deterministic.ps1" not in lowered
    assert "run-us-capacity-pilot.ps1" not in lowered
    assert "2023_5.zip" not in lowered
    _assert_no_all_switch(text)
    assert "chmod " not in lowered
    assert "chown " not in lowered


def test_prepare_operator_is_single_process_stop_point_not_mutation() -> None:
    text = _text(PREPARE)

    assert "[string]$ExpectedMainSha" in text
    assert "origin/main" in text
    assert "EXACT_MAIN_CLEAN_OK" in text
    assert "stop-idle-worker.ps1" in text
    assert "diagnose-clickhouse-active-hot-permissions-v2.ps1" in text
    assert "REVIEW_ACTIVE_HOT_PERMISSION_EVIDENCE" in text
    assert "US_CAPACITY_PILOT_PERMISSION_REVIEW_REQUIRED" in text
    assert "Permission repair: NOT_PERFORMED" in text
    assert "US schema apply: NOT_PERFORMED" in text
    assert "US replay: NOT_PERFORMED" in text

    lowered = text.lower()
    assert "apply-us-m1-schema.ps1" not in lowered
    assert "replay-us-deterministic.ps1" not in lowered
    assert "run-us-capacity-pilot.ps1" not in lowered
    assert "2023_5.zip" not in lowered
    _assert_no_all_switch(text)
