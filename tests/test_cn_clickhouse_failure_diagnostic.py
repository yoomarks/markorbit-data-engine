from pathlib import Path

import pytest

from app.cn.clickhouse_failure import (
    DIAGNOSTIC_VERSION,
    _normalize_since_utc,
    _query_log_sql,
)


def test_cn_failure_diagnostic_is_read_only_and_wired_into_replay():
    diagnostic = Path("app/cn/clickhouse_failure.py").read_text(encoding="utf-8")
    replay = Path("scripts/replay-cn-full.ps1").read_text(encoding="utf-8")
    standalone = Path("scripts/diagnose-cn-clickhouse-failure.ps1").read_text(
        encoding="utf-8"
    )

    assert "SYSTEM FLUSH LOGS" in diagnostic
    assert "FROM system.query_log" in diagnostic
    assert "ExceptionWhileProcessing" in diagnostic
    assert "exception_code" in diagnostic
    assert "query_duration_ms" in diagnostic
    assert "memory_usage" in diagnostic
    assert "query" in diagnostic
    assert "has(databases, 'markorbit_facts')" in diagnostic
    assert "database = 'markorbit_facts'" not in diagnostic
    assert "INSERT INTO" not in diagnostic
    assert "ALTER TABLE" not in diagnostic
    assert "DROP TABLE" not in diagnostic

    assert "python -m app.cn.clickhouse_failure" in replay
    assert '"compose", "run", "--build"' in replay
    assert "$replayStartedUtc" in replay
    assert "--since-utc $replayStartedUtc" in replay
    assert "failure_count=0" in replay

    assert "app.cn.clickhouse_failure" in standalone
    assert '"compose", "run", "--build"' in standalone
    assert '[string]$SinceUtc = ""' in standalone
    assert '$("--since-utc"' not in standalone
    assert '$argsList += @("--since-utc", $SinceUtc)' in standalone


def test_cn_failure_diagnostic_since_utc_is_timezone_safe_and_sql_bounded():
    epoch_seconds, normalized = _normalize_since_utc("2026-08-12T10:49:53.123456Z")

    assert epoch_seconds is not None
    assert normalized == "2026-08-12T10:49:53.123456Z"
    sql = _query_log_sql(99, epoch_seconds)
    assert f"event_time >= toDateTime({epoch_seconds}, 'UTC')" in sql
    assert "LIMIT 20" in sql
    assert DIAGNOSTIC_VERSION == "CN_CLICKHOUSE_FAILURE_DIAGNOSTIC_V3_RUN_SCOPED"


def test_cn_failure_diagnostic_rejects_naive_since_time():
    with pytest.raises(ValueError, match="explicit timezone"):
        _normalize_since_utc("2026-08-12T10:49:53")
