from __future__ import annotations

from pathlib import Path

from app.us.remaining_capacity_inventory import build_remaining_capacity_inventory


def test_inventory_counts_only_unfinished_replay_suffix(tmp_path: Path) -> None:
    completed = tmp_path / "completed.zip"
    remaining_a = tmp_path / "remaining-a.zip"
    remaining_b = tmp_path / "remaining-b.zip"
    completed.write_bytes(b"abc")
    remaining_a.write_bytes(b"12345")
    remaining_b.write_bytes(b"1234567")

    def fake_plan_builder(raw_root: Path, **kwargs):
        assert raw_root == tmp_path
        assert kwargs == {"expected_history_parts": 91, "deep_source_test": False}
        return {
            "status": "READY",
            "safe_to_execute": True,
            "preflight_status": "PASS",
            "success_prefix_count": 1,
            "remaining_count": 2,
            "next_step": {
                "sequence": 2,
                "file_name": remaining_a.name,
                "path": str(remaining_a),
            },
            "blockers": [],
            "steps": [
                {
                    "sequence": 1,
                    "package_kind": "HISTORICAL_APPLICATIONS",
                    "file_name": completed.name,
                    "path": str(completed),
                    "action": "SKIP_SUCCESS",
                },
                {
                    "sequence": 2,
                    "package_kind": "HISTORICAL_APPLICATIONS",
                    "file_name": remaining_a.name,
                    "path": str(remaining_a),
                    "action": "REGISTER_AND_INGEST",
                },
                {
                    "sequence": 3,
                    "package_kind": "DAILY_APPLICATIONS",
                    "file_name": remaining_b.name,
                    "path": str(remaining_b),
                    "action": "REGISTER_AND_INGEST",
                },
            ],
        }

    report = build_remaining_capacity_inventory(
        tmp_path,
        expected_history_parts=91,
        plan_builder=fake_plan_builder,
    )

    assert report["inventory_version"] == "US_REMAINING_CAPACITY_INVENTORY_V1"
    assert report["status"] == "PASS"
    assert report["safe"] is True
    assert report["remaining_count"] == 2
    assert report["remaining_raw_bytes"] == 12
    assert report["success_prefix_count"] == 1
    assert report["source_bytes_already_present_on_raw_storage"] is True
    assert report["incremental_raw_copy_bytes_required_by_replay"] == 0
    assert report["deep_source_test_performed"] is False
    assert report["by_package_kind"] == {
        "DAILY_APPLICATIONS": {"package_count": 1, "raw_bytes": 7},
        "HISTORICAL_APPLICATIONS": {"package_count": 1, "raw_bytes": 5},
    }


def test_inventory_fails_closed_when_replay_plan_is_not_safe(tmp_path: Path) -> None:
    def fake_plan_builder(raw_root: Path, **kwargs):
        assert raw_root == tmp_path
        assert kwargs["deep_source_test"] is False
        return {
            "status": "BLOCKED",
            "safe_to_execute": False,
            "remaining_count": 309,
            "success_prefix_count": 1,
            "next_step": {"sequence": 2},
            "blockers": ["source_preflight_not_safe"],
        }

    report = build_remaining_capacity_inventory(
        tmp_path,
        expected_history_parts=91,
        plan_builder=fake_plan_builder,
    )

    assert report["status"] == "BLOCKED"
    assert report["safe"] is False
    assert report["remaining_raw_bytes"] == 0
    assert report["blockers"] == ["source_preflight_not_safe"]


def test_inventory_fails_closed_when_remaining_file_disappears(tmp_path: Path) -> None:
    missing = tmp_path / "missing.zip"

    def fake_plan_builder(raw_root: Path, **kwargs):
        return {
            "status": "READY",
            "safe_to_execute": True,
            "preflight_status": "PASS",
            "success_prefix_count": 1,
            "remaining_count": 1,
            "next_step": {"sequence": 2, "path": str(missing)},
            "blockers": [],
            "steps": [
                {
                    "sequence": 2,
                    "package_kind": "HISTORICAL_APPLICATIONS",
                    "file_name": missing.name,
                    "path": str(missing),
                    "action": "REGISTER_AND_INGEST",
                }
            ],
        }

    report = build_remaining_capacity_inventory(
        tmp_path,
        expected_history_parts=91,
        plan_builder=fake_plan_builder,
    )

    assert report["status"] == "BLOCKED"
    assert report["safe"] is False
    assert report["blockers"][0]["type"] == "REMAINING_SOURCE_FILE_MISSING"
