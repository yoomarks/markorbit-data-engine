from __future__ import annotations

from pathlib import Path
import shutil
import zipfile

import pytest

from app.scanner import sha256_file
from app.us.stage_sources import apply_staging, build_staging_plan


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


def test_dry_run_reports_archive_sources_without_copying(tmp_path: Path) -> None:
    incoming, archive = _dirs(tmp_path)
    _zip(archive / "apc18840407-20251231-01.zip", "history")
    _zip(archive / "apc260102.zip", "daily")

    report = build_staging_plan(tmp_path, expected_history_parts=1)

    assert report["status"] == "READY"
    assert report["copy_required_count"] == 2
    assert report["conflict_count"] == 0
    assert not (incoming / "apc18840407-20251231-01.zip").exists()
    assert not (incoming / "apc260102.zip").exists()


def test_apply_copies_archive_sources_with_verified_sha_and_canonical_names(tmp_path: Path) -> None:
    incoming, archive = _dirs(tmp_path)
    history = archive / "apc18840407-20251231-01_deadbeef.zip"
    daily = archive / "apc260102_cafebabe.zip"
    _zip(history, "history")
    _zip(daily, "daily")

    report = apply_staging(tmp_path, expected_history_parts=1)

    assert report["status"] == "APPLIED"
    assert report["applied"] is True
    assert report["copied_count"] == 2
    staged_history = incoming / "apc18840407-20251231-01.zip"
    staged_daily = incoming / "apc260102.zip"
    assert staged_history.is_file()
    assert staged_daily.is_file()
    assert sha256_file(staged_history) == sha256_file(history)
    assert sha256_file(staged_daily) == sha256_file(daily)
    assert report["postflight"]["safe_to_replay"] is True
    assert report["postflight"]["archive_staging_required_count"] == 0


def test_apply_is_idempotent_after_successful_stage(tmp_path: Path) -> None:
    _incoming, archive = _dirs(tmp_path)
    _zip(archive / "apc18840407-20251231-01.zip", "history")
    _zip(archive / "apc260102.zip", "daily")

    first = apply_staging(tmp_path, expected_history_parts=1)
    second = apply_staging(tmp_path, expected_history_parts=1)

    assert first["status"] == "APPLIED"
    assert second["status"] == "NOOP"
    assert second["applied"] is False
    assert second["copy_required_count"] == 0
    assert second["preflight"]["safe_to_replay"] is True


def test_identical_incoming_copy_means_no_stage_for_that_source(tmp_path: Path) -> None:
    incoming, archive = _dirs(tmp_path)
    history = archive / "apc18840407-20251231-01.zip"
    daily = archive / "apc260102.zip"
    _zip(history, "history")
    _zip(daily, "daily")
    shutil.copyfile(history, incoming / history.name)

    report = build_staging_plan(tmp_path, expected_history_parts=1)

    assert report["status"] == "READY"
    assert report["copy_required_count"] == 1
    assert report["staging_rows"][0]["canonical_file_name"] == "apc260102.zip"


def test_different_sha_at_canonical_destination_blocks_before_copy(tmp_path: Path) -> None:
    incoming, archive = _dirs(tmp_path)
    _zip(archive / "apc18840407-20251231-01.zip", "history")
    _zip(archive / "apc260102_deadbeef.zip", "archive-daily")
    _zip(incoming / "apc260102.zip", "different-daily")

    plan = build_staging_plan(tmp_path, expected_history_parts=1)

    assert plan["status"] == "BLOCKED"
    assert plan["blocked_reason"] == "source_preflight_not_safe"
    assert "SEMANTIC_PARTITION_SHA_CONFLICT" in plan["preflight"]["hard_issue_types"]
    with pytest.raises(RuntimeError, match="source_preflight_not_safe"):
        apply_staging(tmp_path, expected_history_parts=1)


def test_unpinned_or_incomplete_history_blocks_staging(tmp_path: Path) -> None:
    _incoming, archive = _dirs(tmp_path)
    _zip(archive / "apc18840407-20251231-01.zip", "history")
    _zip(archive / "apc260102.zip", "daily")

    unpinned = build_staging_plan(tmp_path, expected_history_parts=None)
    missing_tail = build_staging_plan(tmp_path, expected_history_parts=2)

    assert unpinned["status"] == "BLOCKED"
    assert "historical_tail_part_count_not_pinned" in unpinned["preflight"]["not_ready_reasons"]
    assert missing_tail["status"] == "BLOCKED"
    assert "expected_historical_parts_missing" in missing_tail["preflight"]["not_ready_reasons"]


def test_old_daily_precedence_failure_blocks_staging(tmp_path: Path) -> None:
    _incoming, archive = _dirs(tmp_path)
    _zip(archive / "apc18840407-20251231-01.zip", "history")
    _zip(archive / "apc251231.zip", "old-daily")

    report = build_staging_plan(tmp_path, expected_history_parts=1)

    assert report["status"] == "BLOCKED"
    assert "DAILY_PACKAGE_NOT_AFTER_HISTORICAL_BASELINE" in report["preflight"]["hard_issue_types"]


def test_apply_refuses_if_archive_source_changes_after_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _incoming, archive = _dirs(tmp_path)
    history = archive / "apc18840407-20251231-01.zip"
    daily = archive / "apc260102.zip"
    _zip(history, "history")
    _zip(daily, "daily")

    from app.us import stage_sources

    original_builder = stage_sources.build_staging_plan
    plan = original_builder(tmp_path, expected_history_parts=1)
    assert plan["status"] == "READY"
    _zip(daily, "changed-after-plan")
    monkeypatch.setattr(stage_sources, "build_staging_plan", lambda *args, **kwargs: plan)

    with pytest.raises(RuntimeError, match="Archive source changed after preflight"):
        stage_sources.apply_staging(tmp_path, expected_history_parts=1)


def test_deep_source_test_is_forwarded_to_preflight(tmp_path: Path) -> None:
    _incoming, archive = _dirs(tmp_path)
    _zip(archive / "apc18840407-20251231-01.zip", "history")
    daily = archive / "apc260102.zip"
    with zipfile.ZipFile(daily, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.xml", _xml("valid"))
        zf.writestr("b.xml", "<broken>")

    shallow = build_staging_plan(tmp_path, expected_history_parts=1, deep_source_test=False)
    deep = build_staging_plan(tmp_path, expected_history_parts=1, deep_source_test=True)

    assert shallow["status"] == "READY"
    assert deep["status"] == "BLOCKED"
    assert "SOURCE_CONTAINER_OR_XML_UNREADABLE" in deep["preflight"]["hard_issue_types"]
