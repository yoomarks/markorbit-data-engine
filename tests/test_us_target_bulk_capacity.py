from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.us.target_bulk_journal as bulk_journal
import app.us.target_bulk_replay as bulk_replay
from app.us.target_bulk_plan import ACCEPTED_SCHEMA_MANIFEST_SHA256
from app.us.target_canary import APPLICATION_CANARY_TABLES


class _CapacityClient:
    def __init__(self, *, total: int, free: int) -> None:
        self.total = total
        self.free = free

    def query(self, sql: str):
        assert sql == "SELECT total_space,free_space FROM system.disks WHERE name='hot_us'"
        return SimpleNamespace(result_rows=[[self.total, self.free]])


def _counts(value: int = 1) -> dict[str, int]:
    return {table: value for table in APPLICATION_CANARY_TABLES}


def _minimal_plan() -> dict:
    # This is only for the injected-callback orchestration test below. Plan validation
    # is patched there because full deterministic-plan validation has dedicated tests.
    return {
        "required_authority_token": "GO #545 test-plan",
        "plan_sha256": "1" * 64,
        "inventory_sha256": "2" * 64,
        "execution_main": "a" * 40,
        "raw_root": "F:/MarkOrbitData/raw",
        "accepted_schema_manifest_sha256": ACCEPTED_SCHEMA_MANIFEST_SHA256,
        "start_sequence": 3,
        "end_sequence": 4,
        "package_count": 3,
        "packages": [
            {
                "sequence": 1,
                "role": "PACKAGE1_TARGET_BRIDGE_REQUIRE_OR_ADOPT",
                "file_name": "p1.zip",
                "sha256": "3" * 64,
                "package_id": "00000000-0000-0000-0000-000000000001",
            },
            {
                "sequence": 3,
                "role": "BOUNDED_SUFFIX",
                "file_name": "p3.zip",
                "sha256": "4" * 64,
                "package_id": "00000000-0000-0000-0000-000000000003",
            },
            {
                "sequence": 4,
                "role": "BOUNDED_SUFFIX",
                "file_name": "p4.zip",
                "sha256": "5" * 64,
                "package_id": "00000000-0000-0000-0000-000000000004",
            },
        ],
    }


def test_hot_us_headroom_accepts_exact_30_percent_floor() -> None:
    report = bulk_replay._verify_hot_us_headroom(
        _CapacityClient(total=1_000, free=300)
    )
    assert report == {
        "total_bytes": 1_000,
        "free_bytes": 300,
        "minimum_free_ratio": 0.30,
        "minimum_free_bytes": 300,
        "floor_satisfied": True,
    }


def test_hot_us_headroom_fails_closed_below_floor() -> None:
    with pytest.raises(RuntimeError, match="below the accepted floor"):
        bulk_replay._verify_hot_us_headroom(
            _CapacityClient(total=1_000, free=299)
        )


def test_hot_us_headroom_rejects_invalid_disk_shape() -> None:
    class BadClient:
        def query(self, sql: str):
            return SimpleNamespace(result_rows=[])

    with pytest.raises(RuntimeError, match="unexpected shape"):
        bulk_replay._verify_hot_us_headroom(BadClient())


def test_capacity_guard_failure_blocks_before_next_package(tmp_path, monkeypatch) -> None:
    plan = _minimal_plan()
    journal_path = tmp_path / "bulk.json"
    state_dir = tmp_path / "state"
    calls: list[tuple[str, int]] = []
    guard_calls = 0

    monkeypatch.setattr(bulk_replay, "validate_bulk_plan", lambda plan: None)
    monkeypatch.setattr(bulk_journal, "validate_bulk_plan", lambda plan: None)
    monkeypatch.setattr(
        bulk_replay,
        "_verify_package2_target",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        bulk_replay,
        "audit_bulk_plan",
        lambda **kwargs: {"journal_state": "COMPLETE"},
    )

    def commit(item, state):
        sequence = int(item["sequence"])
        calls.append(("commit", sequence))
        return _counts(sequence)

    def cleanup(item, state, counts):
        calls.append(("cleanup", int(item["sequence"])))

    def guard():
        nonlocal guard_calls
        guard_calls += 1
        # Package 1: pre + post pass. Package 3: pre passes, post-cleanup fails.
        if guard_calls == 4:
            raise RuntimeError("hot_us below floor")

    with pytest.raises(RuntimeError, match="hot_us below floor"):
        bulk_replay.execute_bulk_plan(
            plan=plan,
            stage2_receipt={},
            journal_path=journal_path,
            state_dir=state_dir,
            authority_token=plan["required_authority_token"],
            commit_package=commit,
            cleanup_package=cleanup,
            capacity_guard=guard,
        )

    assert calls == [
        ("commit", 1),
        ("cleanup", 1),
        ("commit", 3),
        ("cleanup", 3),
    ]
    journal = bulk_journal.load_bulk_journal(journal_path, plan=plan)
    assert journal["state"] == "BLOCKED"
    assert journal["blocked"]["sequence"] == 3
    assert journal["packages"]["1"]["status"] == "COMPLETE"
    assert journal["packages"]["3"]["status"] == "FINAL_VERIFIED"
    assert journal["packages"]["4"]["status"] == "PENDING"
