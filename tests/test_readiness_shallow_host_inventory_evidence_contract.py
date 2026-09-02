from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "profile-production-multi-disk-migration-readiness.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_readiness_uses_shallow_isolated_host_inventory_root() -> None:
    text = source()
    assert "function Get-ShallowHostInventoryRoot" in text
    assert "Join-Path 'reports' (Join-Path '_hi' $RunId)" in text
    assert "host_inventory_evidence_strategy=SHALLOW_REPO_REPORTS" in text
    assert "strategy='SHALLOW_REPO_REPORTS'" in text
    assert "receipt_path=[string]$inventoryResult['receipt_path']" in text
    assert "$inventoryResult = Invoke-HostInventory" in text
    assert "$inventory = $inventoryResult['report']" in text
    assert "$inventoryRoot = Join-Path $evidenceDir 'inventory'" not in text


def test_regression_old_rebalance_nesting_exceeded_ps51_path_envelope() -> None:
    repo = r"D:\yoomarks\markorbit-data-engine"
    old_receipt = (
        repo
        + r"\reports\production_storage_rebalance_inventory_20260902_090301"
        + r"\sizing\production_hot_warm_sizing_20260902_090304"
        + r"\readiness\production_multi_disk_migration_readiness_20260902_090306"
        + r"\inventory\global_multi_disk_host_inventory_20260902_090308"
        + r"\global_multi_disk_host_inventory.json"
    )
    assert len(old_receipt) > 260


def test_shallow_inventory_receipt_has_large_ps51_path_margin() -> None:
    repo = r"D:\yoomarks\markorbit-data-engine"
    shallow_receipt = (
        repo
        + r"\reports\_hi\20260902_090308123_123456"
        + r"\global_multi_disk_host_inventory_20260902_090308"
        + r"\global_multi_disk_host_inventory.json"
    )
    assert len(shallow_receipt) < 200


def test_shallow_fix_preserves_read_only_safety_boundary() -> None:
    text = source()
    for marker in (
        "live_migration_authorized=$false",
        "vhdx_create_authorized=$false",
        "source_volume_delete_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
        "source_copy_performed=$false",
        "corpus_replay_performed=$false",
    ):
        assert marker in text
