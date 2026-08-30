from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "recover-clickhouse-schema-version-tmp-via-restart.ps1"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_native_tmp_recovery_is_exact_incident_scoped() -> None:
    text = _text()

    assert 'ExpectedSchemaUuid = "7716c662-1886-4e4b-a7e2-631c80ac8dd2"' in text
    assert '"tmp_insert_all_1_1_0"' in text
    assert '"tmp_insert_all_2_2_0"' in text
    assert '"tmp_insert_all_3_3_0"' in text
    assert 'ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545"' in text
    assert "EXACT_TMP_SET_MATCH_OK" in text
    assert "schema_version UUID drift detected" in text
    assert "Compare-Object -ReferenceObject $expectedTmp -DifferenceObject $actualTmp" in text
    assert "This recovery operator is frozen to ClickHouse 24.8.x" in text


def test_native_tmp_recovery_requires_global_idle_and_zero_workers() -> None:
    text = _text()

    assert "stop-idle-worker.ps1" in text
    assert "docker compose ps -a -q worker" in text
    assert "worker_container_count_all_states=" in text
    assert "ZERO_WORKER_CONTAINERS_OK" in text
    assert "system.mutations" in text
    assert "system.processes" in text
    assert "ClickHouse is not idle enough for a controlled native tmp recovery restart" in text


def test_native_tmp_recovery_waits_for_docker_health_before_any_table_query() -> None:
    text = _text()

    assert "function Wait-ClickHouseHealthy" in text
    assert "docker inspect --format" in text
    assert ".State.Health.Status" in text
    assert "clickhouse_docker_health=healthy" in text
    assert "CLICKHOUSE_PREFLIGHT_HEALTHY_OK" in text
    assert "CLICKHOUSE_CONTROLLED_RESTART_READY_OK" in text
    assert 'clickhouse-client --query "SELECT 1"' not in text

    preflight_call = text.index('$null = Wait-ClickHouseHealthy -Phase "preflight"')
    pre_restart_query = text.index('$clickhouseVersion = Invoke-ClickHouseScalar "SELECT version()"')
    assert preflight_call < pre_restart_query


def test_native_tmp_recovery_surfaces_startup_diagnostics_on_health_timeout() -> None:
    text = _text()

    assert "ClickHouseHealthTimeoutSeconds = 600" in text
    assert "function Show-ClickHouseStartupDiagnostics" in text
    assert "clickhouse_container_state=" in text
    assert "clickhouse_health_log=" in text
    assert "docker compose logs --tail 120 --no-color clickhouse" in text
    assert "clickhouse_startup_log=" in text
    assert "CLICKHOUSE_STARTUP_DIAGNOSTICS_COMPLETE" in text
    assert "Show-ClickHouseStartupDiagnostics -Phase $Phase -ContainerId $lastContainerId" in text


def test_native_tmp_recovery_is_idempotent_after_interrupted_restart() -> None:
    text = _text()

    assert "if ($actualTmp.Count -eq 0)" in text
    assert "ALREADY_RECOVERED_AFTER_INTERRUPTED_RESTART" in text
    assert "ZERO_TMP_ALREADY_RECOVERED_OK" in text
    assert "$schemaSnapshotBefore -ne $ExpectedSchemaSnapshot" in text
    assert "$schemaSnapshotAfter -ne $ExpectedSchemaSnapshot" in text
    assert "already_recovered_after_interrupted_restart=$alreadyRecovered" in text
    assert "schema_version tmp_insert set is neither the frozen incident set nor the fully recovered zero-tmp state" in text


def test_native_tmp_recovery_uses_clickhouse_restart_not_filesystem_delete() -> None:
    text = _text()
    lowered = text.lower()

    assert "docker compose stop clickhouse" in text
    assert "docker compose start clickhouse" in text
    assert "CLICKHOUSE_CONTROLLED_STOP_OK" in text
    assert "tmp_insert_count_after=" in text
    assert "CLICKHOUSE_NATIVE_TMP_RECOVERY_PASS" in text
    assert "manual_filesystem_cleanup_performed=False" in text

    assert "remove-item" not in lowered
    assert "rm -rf" not in lowered
    assert " rmdir " not in lowered
    assert "unlink" not in lowered
    assert "chmod " not in lowered
    assert "chown " not in lowered
    assert "alter table" not in lowered
    assert "drop table" not in lowered
    assert "truncate table" not in lowered


def test_native_tmp_recovery_preserves_schema_snapshot_and_reruns_hot_v2() -> None:
    text = _text()

    assert "schema_version_snapshot_current=" in text
    assert "schema_version_snapshot_after=" in text
    assert "diagnose-clickhouse-active-hot-permissions-v2.ps1" in text
    assert "post_recovery_permission_blockers=0" in text
    assert "disposable_root_rename_probe.passed" in text
    assert "cn_comparison.rwx_for_server_identity" in text


def test_native_tmp_recovery_fail_safe_avoids_duplicate_start_when_running() -> None:
    text = _text()

    assert "docker compose ps --status running -q clickhouse" in text
    assert "ClickHouse is already running; fail-safe duplicate start is not needed." in text
    assert "Attempting fail-safe ClickHouse start after an interrupted recovery..." in text


def test_native_tmp_recovery_does_not_advance_us_or_worker_lifecycle() -> None:
    text = _text()
    lowered = text.lower()

    assert "worker_start_performed=False" in text
    assert "schema_apply_performed=False" in text
    assert "corpus_replay_performed=False" in text
    assert "permission_repair_performed=False" in text

    assert "docker compose start worker" not in lowered
    assert "docker compose restart worker" not in lowered
    assert "apply-us-m1-schema.ps1" not in lowered
    assert "replay-us-deterministic.ps1" not in lowered
    assert "run-us-capacity-pilot.ps1" not in lowered
    assert "2023_5.zip" not in lowered
    assert re.search(r"(?i)(?<![A-Za-z0-9_-])-All(?![A-Za-z0-9_-])", text) is None
