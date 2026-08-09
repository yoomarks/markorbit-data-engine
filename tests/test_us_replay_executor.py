from __future__ import annotations

from pathlib import Path
import zipfile

from app.us.migrations import US_SCHEMA_VERSION
from app.us.replay_executor import build_replay_plan
from app.us.source_preflight import build_preflight


def _dirs(root: Path) -> tuple[Path, Path]:
    incoming = root / "incoming" / "us"
    archive = root / "archive" / "us"
    incoming.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    return incoming, archive


def _xml(marker: str) -> str:
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<trademark-case-files><case-file>"
        f"<serial-number>{marker}</serial-number>"
        "</case-file></trademark-case-files>"
    )


def _zip(path: Path, marker: str) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.xml", _xml(marker))


def _source_set(root: Path) -> list[dict]:
    incoming, _archive = _dirs(root)
    _zip(incoming / "apc18840407-20251231-01.zip", "history")
    _zip(incoming / "apc260102.zip", "daily")
    report = build_preflight(root, expected_history_parts=1)
    assert report["safe_to_replay"] is True
    return report["replay_plan"]


def _registry_row(
    source: dict,
    *,
    status: str,
    rank: int,
    package_id: str,
    profile_version: str = US_SCHEMA_VERSION,
    profile_sha: str | None = None,
) -> dict:
    digest = str(source["sha256"]).lower()
    return {
        "package_id": package_id,
        "package_sequence": rank,
        "file_name": source["file_name"],
        "file_path": source["path"],
        "file_size": 1,
        "sha256": digest,
        "package_kind": source["package_kind"],
        "partition_dimension": "TEST",
        "partition_value": source["partition_value"],
        "source_period_start": None,
        "source_period_end": None,
        "source_sequence": rank,
        "source_rank": rank,
        "status": status,
        "profile": (
            {
                "source_sha256": profile_sha if profile_sha is not None else digest,
                "totals": {"schema_version": profile_version},
            }
            if status == "SUCCESS"
            else {}
        ),
        "schema_version": US_SCHEMA_VERSION,
        "archived_path": None,
        "processed_at": None,
        "error_message": None,
    }


def test_empty_registry_starts_with_register_and_ingest(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=[],
    )

    assert plan["status"] == "READY"
    assert plan["remaining_count"] == 2
    assert plan["next_step"]["sequence"] == 1
    assert plan["next_step"]["action"] == "REGISTER_AND_INGEST"
    assert plan["next_step"]["sha256"] == sources[0]["sha256"]


def test_successful_prefix_is_skipped_and_next_package_is_selected(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    registry = [
        _registry_row(
            sources[0],
            status="SUCCESS",
            rank=100,
            package_id="00000000-0000-0000-0000-000000000001",
        )
    ]

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
    )

    assert plan["status"] == "READY"
    assert plan["steps"][0]["action"] == "SKIP_SUCCESS"
    assert plan["next_step"]["sequence"] == 2
    assert plan["next_step"]["action"] == "REGISTER_AND_INGEST"


def test_failed_prefix_package_is_retried_before_later_package(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    registry = [
        _registry_row(
            sources[0],
            status="FAILED",
            rank=100,
            package_id="00000000-0000-0000-0000-000000000001",
        )
    ]

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
    )

    assert plan["status"] == "READY"
    assert plan["next_step"]["sequence"] == 1
    assert plan["next_step"]["action"] == "RETRY_FULL_PACKAGE"
    assert plan["steps"][1]["action"] == "REGISTER_AND_INGEST"


def test_processing_package_is_recoverable_on_apply_not_skipped(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    registry = [
        _registry_row(
            sources[0],
            status="PROCESSING",
            rank=100,
            package_id="00000000-0000-0000-0000-000000000001",
        )
    ]

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
    )

    assert plan["status"] == "READY"
    assert plan["next_step"]["action"] == "RECOVER_AND_RETRY"


def test_later_success_cannot_skip_earlier_unfinished_package(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    registry = [
        _registry_row(
            sources[1],
            status="SUCCESS",
            rank=300,
            package_id="00000000-0000-0000-0000-000000000002",
        )
    ]

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
    )

    assert plan["status"] == "BLOCKED"
    assert "out_of_order_success_package" in plan["blockers"]


def test_stale_success_profile_cannot_be_silently_skipped(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    registry = [
        _registry_row(
            sources[0],
            status="SUCCESS",
            rank=100,
            package_id="00000000-0000-0000-0000-000000000001",
            profile_version="US_M1.2",
        )
    ]

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
    )

    assert plan["status"] == "BLOCKED"
    assert "successful_package_requires_m13_replay" in plan["blockers"]


def test_success_profile_sha_must_match_authoritative_source(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    registry = [
        _registry_row(
            sources[0],
            status="SUCCESS",
            rank=100,
            package_id="00000000-0000-0000-0000-000000000001",
            profile_sha="0" * 64,
        )
    ]

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
    )

    assert plan["status"] == "BLOCKED"
    assert "successful_package_requires_m13_replay" in plan["blockers"]


def test_registered_package_not_in_source_plan_blocks_execution(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    extra = _registry_row(
        sources[0],
        status="REGISTERED",
        rank=100,
        package_id="00000000-0000-0000-0000-000000000099",
    )
    extra["sha256"] = "f" * 64

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=[extra],
    )

    assert plan["status"] == "BLOCKED"
    assert "registered_us_package_not_in_source_plan" in plan["blockers"]


def test_registry_identity_mismatch_blocks_execution(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    row = _registry_row(
        sources[0],
        status="REGISTERED",
        rank=100,
        package_id="00000000-0000-0000-0000-000000000001",
    )
    row["partition_value"] = "wrong-partition"

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=[row],
    )

    assert plan["status"] == "BLOCKED"
    assert "registry_source_identity_mismatch" in plan["blockers"]


def test_existing_registry_ranks_must_preserve_source_plan_order(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    registry = [
        _registry_row(
            sources[0],
            status="SUCCESS",
            rank=300,
            package_id="00000000-0000-0000-0000-000000000001",
        ),
        _registry_row(
            sources[1],
            status="REGISTERED",
            rank=200,
            package_id="00000000-0000-0000-0000-000000000002",
        ),
    ]

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
    )

    assert plan["status"] == "BLOCKED"
    assert "registered_source_rank_order_violation" in plan["blockers"]


def test_pending_archive_only_source_requires_explicit_staging(tmp_path: Path) -> None:
    _incoming, archive = _dirs(tmp_path)
    _zip(archive / "apc18840407-20251231-01.zip", "history")
    _zip(archive / "apc260102.zip", "daily")

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=[],
    )

    assert plan["status"] == "BLOCKED"
    assert "pending_source_requires_archive_staging" in plan["blockers"]
    assert len(plan["blocker_details"]["staging_required"]) == 2


def test_unsafe_source_preflight_blocks_replay_before_registry_actions(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "history")
    _zip(incoming / "apc18840407-20251231-03.zip", "history3")

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=3,
        registry_rows=[],
    )

    assert plan["status"] == "BLOCKED"
    assert "source_preflight_not_safe" in plan["blockers"]


def test_all_success_with_current_profiles_is_complete(tmp_path: Path) -> None:
    sources = _source_set(tmp_path)
    registry = [
        _registry_row(
            sources[0],
            status="SUCCESS",
            rank=100,
            package_id="00000000-0000-0000-0000-000000000001",
        ),
        _registry_row(
            sources[1],
            status="SUCCESS",
            rank=300,
            package_id="00000000-0000-0000-0000-000000000002",
        ),
    ]

    plan = build_replay_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
    )

    assert plan["status"] == "COMPLETE"
    assert plan["remaining_count"] == 0
    assert plan["next_step"] is None
    assert plan["acceptance_required_after_complete"] is True
