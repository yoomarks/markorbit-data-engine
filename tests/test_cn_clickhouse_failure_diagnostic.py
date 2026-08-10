from pathlib import Path


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
    assert "app.cn.clickhouse_failure" in standalone
    assert "compose run --build" in standalone
