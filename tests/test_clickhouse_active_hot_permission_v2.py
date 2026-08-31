from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "scripts" / "diagnose-clickhouse-active-hot-permissions-v2.ps1"
PREPARE = ROOT / "scripts" / "prepare-us-capacity-pilot-target-host.ps1"


def test_v2_extends_read_only_hot_permission_evidence() -> None:
    text = V2.read_text(encoding="utf-8")

    assert "diagnose-clickhouse-active-hot-permissions.ps1" in text
    assert "CN_COMPARISON_PATH_V1" in text
    assert "cn_comparison_path_stat" in text
    assert "cn_comparison_rwx_for_server_identity" in text
    assert "schema_path_stat" in text
    assert "schema_tmp_insert_stat" in text
    assert "Repair attempted: False" in text
    assert "Safe to apply schema: False" in text
    assert "ACTIVE_HOT_PERMISSION_DIAGNOSTIC_V2_COMPLETE" in text

    lowered = text.lower()
    for forbidden in (
        "chmod ",
        "chown ",
        "alter table",
        "drop table",
        "truncate table",
        "docker compose start worker",
        "docker compose restart worker",
        "apply-us-m1-schema",
        "replay-us-deterministic",
        "2023_5.zip",
    ):
        assert forbidden not in lowered


def test_prepare_uses_linux_volume_contract_and_still_stops_before_mutation() -> None:
    text = PREPARE.read_text(encoding="utf-8")

    assert "assert-clickhouse-active-hot-storage-contract.ps1" in text
    assert "diagnose-clickhouse-active-hot-permissions-v2.ps1" not in text
    assert "CLICKHOUSE_ACTIVE_DATA_STORAGE_CONTRACT_V2" in text
    assert "LINUX_VOLUME_STORAGE_ACCEPTED_REVIEW_US_TRANSITION" in text
    assert "US_CAPACITY_PILOT_STORAGE_CONTRACT_READY" in text
    assert "US schema apply: NOT_PERFORMED" in text
    assert "US replay: NOT_PERFORMED" in text
