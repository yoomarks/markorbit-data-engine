from pathlib import Path


STAGE2_OPERATOR = Path("scripts/run-production-us-application-canary-stage2.ps1")


def test_stage2_operator_uses_installed_clickhouse_multicall_client() -> None:
    text = STAGE2_OPERATOR.read_text(encoding="utf-8").lower()
    assert "-- clickhouse client --host" in text
    assert "clickhouse-client" not in text
