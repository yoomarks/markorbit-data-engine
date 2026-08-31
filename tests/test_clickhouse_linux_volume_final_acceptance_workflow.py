from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "clickhouse-linux-volume-final-acceptance-runtime.yml"


def test_final_acceptance_runtime_executes_real_clickhouse_24_8_probe() -> None:
    t = WORKFLOW.read_text(encoding="utf-8")
    assert "clickhouse/clickhouse-server:24.8" in t
    assert "base64 -d | timeout 30s clickhouse-client" in t
    assert "ENGINE=MergeTree" in t
    assert "INSERT INTO markorbit_final_acceptance_ci.t VALUES (1)" in t
    assert "grep -Fx '1'" in t
    assert "EXISTS DATABASE markorbit_final_acceptance_ci" in t
    assert "CLICKHOUSE_24_8_DECODED_BASE64_MERGETREE_ACCEPTANCE_PASS" in t


def test_final_acceptance_runtime_has_ps51_and_concurrency_governance() -> None:
    t = WORKFLOW.read_text(encoding="utf-8")
    assert "shell: powershell" in t
    assert "PSVersionTable.PSVersion.Major -ne 5" in t
    assert "concurrency:" in t
    assert "cancel-in-progress:" in t
    assert "ExpectedMainSha" in t
