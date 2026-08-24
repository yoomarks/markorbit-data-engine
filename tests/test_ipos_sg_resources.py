from collections import namedtuple
from pathlib import Path

from app.snapshot_delta.ipos_sg_resources import build_ipos_storage_preflight


DiskUsage = namedtuple("DiskUsage", "total used free")


def test_storage_preflight_uses_bootstrap_floor(tmp_path: Path):
    preflight = build_ipos_storage_preflight(
        tmp_path,
        minimum_free_bytes=8 * 1024**3,
        disk_usage=lambda _path: DiskUsage(100 * 1024**3, 90 * 1024**3, 10 * 1024**3),
    )

    assert preflight.status == "PASS"
    assert preflight.safe_to_run is True
    assert preflight.largest_retained_snapshot_bytes == 0
    assert preflight.required_free_bytes == 8 * 1024**3


def test_storage_preflight_scales_to_double_largest_retained_snapshot(tmp_path: Path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir(parents=True)
    current = snapshots / "current.csv"
    with current.open("wb") as target:
        target.truncate(6 * 1024**3)

    preflight = build_ipos_storage_preflight(
        tmp_path,
        minimum_free_bytes=8 * 1024**3,
        disk_usage=lambda _path: DiskUsage(100 * 1024**3, 80 * 1024**3, 20 * 1024**3),
    )

    assert preflight.status == "PASS"
    assert preflight.largest_retained_snapshot_bytes == 6 * 1024**3
    assert preflight.required_free_bytes == 12 * 1024**3


def test_storage_preflight_blocks_before_network_when_headroom_is_insufficient(tmp_path: Path):
    preflight = build_ipos_storage_preflight(
        tmp_path,
        disk_usage=lambda _path: DiskUsage(20 * 1024**3, 14 * 1024**3, 6 * 1024**3),
    )

    assert preflight.status == "BLOCKED"
    assert preflight.safe_to_run is False
    assert preflight.free_bytes == 6 * 1024**3
    assert preflight.required_free_bytes == 8 * 1024**3
