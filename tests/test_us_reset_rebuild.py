from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from app.us.package_meta import infer_us_package_descriptor
from app.us.reset_rebuild import (
    RESET_CONFIRMATION,
    apply_reset,
    build_reset_plan,
)
from app.us.source_preflight import build_preflight


US_TABLES = (
    "us_case_current",
    "us_owner_current",
    "us_classification_current",
    "us_event_history",
    "us_statement_current",
    "us_correspondent_current",
    "us_design_search_current",
    "us_prior_registration_current",
    "us_foreign_application_current",
    "us_madrid_filing_current",
    "us_madrid_event_history",
)


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


def _sources(root: Path) -> list[dict]:
    incoming, _archive = _dirs(root)
    _zip(incoming / "apc18840407-20251231-01.zip", "history")
    _zip(incoming / "apc260102.zip", "daily")
    preflight = build_preflight(root, expected_history_parts=1)
    assert preflight["safe_to_replay"] is True
    return preflight["replay_plan"]


def _registry_row(source: dict, *, package_sequence: int, status: str = "SUCCESS") -> dict:
    path = Path(source["path"])
    descriptor = infer_us_package_descriptor(path)
    return {
        "package_id": f"00000000-0000-0000-0000-{package_sequence:012d}",
        "package_sequence": package_sequence,
        "file_name": source["file_name"],
        "file_path": source["path"],
        "file_size": path.stat().st_size,
        "sha256": source["sha256"],
        "package_kind": source["package_kind"],
        "partition_dimension": source.get("partition_dimension") or "",
        "partition_value": source["partition_value"],
        "source_period_start": descriptor.source_period_start,
        "source_period_end": descriptor.source_period_end,
        "source_sequence": descriptor.source_sequence,
        "source_rank": descriptor.source_rank(package_sequence),
        "status": status,
        "profile": {"totals": {"schema_version": "US_M1.3"}},
        "schema_version": "US_M1.3",
        "archived_path": None,
        "processed_at": None,
        "error_message": None,
    }


def _counts(value: int = 0) -> dict[str, int]:
    return {table: value for table in US_TABLES}


def test_empty_us_state_is_noop_when_sources_are_safe(tmp_path: Path) -> None:
    _sources(tmp_path)

    plan = build_reset_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=[],
        table_counts=_counts(0),
    )

    assert plan["status"] == "NOOP"
    assert plan["safe_to_reset"] is True
    assert plan["apply_required"] is False
    assert plan["registered_package_count"] == 0
    assert plan["total_fact_rows"] == 0


def test_existing_us_state_is_ready_and_preserves_package_identity(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    registry = [
        _registry_row(sources[0], package_sequence=11),
        _registry_row(sources[1], package_sequence=12),
    ]
    counts = _counts(0)
    counts["us_case_current"] = 20
    counts["us_owner_current"] = 10

    plan = build_reset_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
        table_counts=counts,
    )

    assert plan["status"] == "READY"
    assert plan["apply_required"] is True
    assert plan["registered_plan_package_count"] == 2
    assert plan["unregistered_source_count"] == 0
    assert plan["total_fact_rows"] == 30
    assert [row["package_id"] for row in plan["reset_rows"]] == [
        registry[0]["package_id"],
        registry[1]["package_id"],
    ]
    assert plan["reset_rows"][0]["source_rank"] < plan["reset_rows"][1]["source_rank"]


def test_unregistered_sources_are_not_fabricated_by_reset(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    registry = [_registry_row(sources[0], package_sequence=11)]

    plan = build_reset_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
        table_counts=_counts(5),
    )

    assert plan["status"] == "READY"
    assert plan["registered_plan_package_count"] == 1
    assert plan["unregistered_source_count"] == 1
    assert len(plan["reset_rows"]) == 1


def test_unsafe_source_preflight_blocks_reset(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "history")
    _zip(incoming / "apc18840407-20251231-03.zip", "history3")

    plan = build_reset_plan(
        tmp_path,
        expected_history_parts=3,
        registry_rows=[],
        table_counts=_counts(1),
    )

    assert plan["status"] == "BLOCKED"
    assert "source_preflight_not_safe" in plan["blockers"]


def test_archive_only_sources_must_be_staged_before_destructive_reset(tmp_path: Path) -> None:
    _incoming, archive = _dirs(tmp_path)
    _zip(archive / "apc18840407-20251231-01.zip", "history")
    _zip(archive / "apc260102.zip", "daily")

    plan = build_reset_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=[],
        table_counts=_counts(1),
    )

    assert plan["status"] == "BLOCKED"
    assert "archive_sources_must_be_staged_before_reset" in plan["blockers"]


def test_registry_package_outside_source_plan_blocks_reset(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    extra = _registry_row(sources[0], package_sequence=99)
    extra["sha256"] = "f" * 64

    plan = build_reset_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=[extra],
        table_counts=_counts(1),
    )

    assert plan["status"] == "BLOCKED"
    assert "registered_us_package_not_in_source_plan" in plan["blockers"]


def test_registry_source_identity_mismatch_blocks_reset(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    row = _registry_row(sources[0], package_sequence=11)
    row["partition_value"] = "wrong"

    plan = build_reset_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=[row],
        table_counts=_counts(1),
    )

    assert plan["status"] == "BLOCKED"
    assert "registry_source_identity_mismatch" in plan["blockers"]


def test_manifest_fingerprint_is_stable_for_same_evidence(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    registry = [_registry_row(sources[0], package_sequence=11)]
    counts = _counts(3)

    first = build_reset_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
        table_counts=counts,
    )
    second = build_reset_plan(
        tmp_path,
        expected_history_parts=1,
        registry_rows=registry,
        table_counts=counts,
    )

    assert first["manifest_fingerprint"] == second["manifest_fingerprint"]


def test_python_apply_requires_exact_confirmation_before_any_reset_work(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=RESET_CONFIRMATION):
        apply_reset(
            tmp_path,
            expected_history_parts=1,
            confirmation="wrong-token",
        )
