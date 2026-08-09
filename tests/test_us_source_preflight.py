from __future__ import annotations

from pathlib import Path
import shutil
import zipfile

from app.us.source_preflight import build_preflight


def _dirs(root: Path) -> tuple[Path, Path]:
    incoming = root / "incoming" / "us"
    archive = root / "archive" / "us"
    incoming.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    return incoming, archive


def _xml(marker: str = "1") -> str:
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<trademark-case-files><case-file>"
        f"<serial-number>{marker}</serial-number>"
        "</case-file></trademark-case-files>"
    )


def _zip(path: Path, marker: str = "1", *, member: str = "data.xml") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, _xml(marker))


def test_preflight_builds_deterministic_history_then_daily_replay_plan(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-02.zip", "h2")
    _zip(incoming / "apc260102.zip", "d1")
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")

    report = build_preflight(tmp_path, expected_history_parts=2)

    assert report["status"] == "PASS"
    assert report["safe_to_replay"] is True
    assert report["historical_baseline_end"] == "2025-12-31"
    assert report["historical_part_completeness"]["complete"] is True
    assert [row["file_name"] for row in report["replay_plan"]] == [
        "apc18840407-20251231-01.zip",
        "apc18840407-20251231-02.zip",
        "apc260102.zip",
    ]
    assert [row["sequence"] for row in report["replay_plan"]] == [1, 2, 3]


def test_preflight_is_not_ready_when_history_tail_count_is_unpinned(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    _zip(incoming / "apc260102.zip", "d1")

    report = build_preflight(tmp_path)

    assert report["status"] == "NOT_READY"
    assert report["safe_to_replay"] is False
    assert "historical_tail_part_count_not_pinned" in report["not_ready_reasons"]


def test_preflight_is_not_ready_for_missing_history_part(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    _zip(incoming / "apc18840407-20251231-03.zip", "h3")
    _zip(incoming / "apc260102.zip", "d1")

    report = build_preflight(tmp_path, expected_history_parts=3)

    assert report["status"] == "NOT_READY"
    assert "historical_part_sequence_incomplete" in report["not_ready_reasons"]
    assert "expected_historical_parts_missing" in report["not_ready_reasons"]
    assert report["historical_part_completeness"]["missing_expected_parts"] == [2]


def test_identical_incoming_and_archive_copy_is_deduplicated_not_failed(tmp_path: Path) -> None:
    incoming, archive = _dirs(tmp_path)
    history = incoming / "apc18840407-20251231-01.zip"
    daily = incoming / "apc260102.zip"
    _zip(history, "h1")
    _zip(daily, "d1")
    shutil.copyfile(daily, archive / daily.name)

    report = build_preflight(tmp_path, expected_history_parts=1)

    assert report["status"] == "PASS_WITH_WARNINGS"
    assert report["safe_to_replay"] is True
    assert "identical_semantic_source_copies_deduplicated" in report["warning_reasons"]
    daily_group = next(
        row
        for row in report["source_inventory"]["semantic_groups"]
        if row["package_kind"] == "DAILY_APPLICATIONS"
    )
    assert daily_group["copy_count"] == 2
    assert daily_group["distinct_sha256_count"] == 1
    assert daily_group["selected_location"] == "incoming"


def test_same_semantic_partition_with_different_sha_is_hard_fail(tmp_path: Path) -> None:
    incoming, archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    _zip(incoming / "apc260102.zip", "daily-a")
    _zip(archive / "apc260102.zip", "daily-b")

    report = build_preflight(tmp_path, expected_history_parts=1)

    assert report["status"] == "FAIL"
    assert report["safe_to_replay"] is False
    assert "SEMANTIC_PARTITION_SHA_CONFLICT" in report["hard_issue_types"]


def test_daily_package_on_or_before_history_baseline_is_hard_fail(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    _zip(incoming / "apc251231.zip", "old-daily")

    report = build_preflight(tmp_path, expected_history_parts=1)

    assert report["status"] == "FAIL"
    assert "DAILY_PACKAGE_NOT_AFTER_HISTORICAL_BASELINE" in report["hard_issue_types"]
    assert report["daily_safety"]["unsafe_on_or_before_baseline"][0]["update_date"] == "2025-12-31"


def test_archive_digest_suffix_is_recovered_and_marked_for_staging(tmp_path: Path) -> None:
    incoming, archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    _zip(archive / "apc260102_deadbeef.zip", "d1")

    report = build_preflight(tmp_path, expected_history_parts=1)

    assert report["status"] == "PASS"
    daily = next(row for row in report["replay_plan"] if row["package_kind"] == "DAILY_APPLICATIONS")
    assert daily["file_name"] == "apc260102_deadbeef.zip"
    assert daily["location"] == "archive"
    assert daily["needs_staging_from_archive"] is True
    assert report["archive_staging_required_count"] == 1


def test_corrupt_zip_is_hard_fail(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    (incoming / "apc260102.zip").write_bytes(b"not a zip")

    report = build_preflight(tmp_path, expected_history_parts=1)

    assert report["status"] == "FAIL"
    assert "SOURCE_CONTAINER_OR_XML_UNREADABLE" in report["hard_issue_types"]


def test_zip_without_xml_is_hard_fail(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    with zipfile.ZipFile(incoming / "apc260102.zip", "w") as archive:
        archive.writestr("readme.txt", "no xml")

    report = build_preflight(tmp_path, expected_history_parts=1)

    assert report["status"] == "FAIL"
    assert "SOURCE_CONTAINER_OR_XML_UNREADABLE" in report["hard_issue_types"]


def test_unknown_package_name_and_gzip_are_hard_fail(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    _zip(incoming / "mystery.zip", "m")
    (incoming / "apc260102.gz").write_bytes(b"gzip-placeholder")

    report = build_preflight(tmp_path, expected_history_parts=1)

    assert report["status"] == "FAIL"
    assert "UNKNOWN_US_PACKAGE_PRECEDENCE" in report["hard_issue_types"]
    assert "UNSUPPORTED_US_SOURCE_SUFFIX" in report["hard_issue_types"]


def test_daily_calendar_gaps_are_informational_not_missing_file_claims(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    _zip(incoming / "apc260102.zip", "d1")
    _zip(incoming / "apc260105.zip", "d2")

    report = build_preflight(tmp_path, expected_history_parts=1)

    assert report["status"] == "PASS"
    gaps = report["daily_safety"]["informational_calendar_gaps"]
    assert gaps == [
        {
            "previous": "2026-01-02",
            "next": "2026-01-05",
            "calendar_gap_days": 2,
            "note": gaps[0]["note"],
        }
    ]
    assert "Informational only" in gaps[0]["note"]


def test_no_daily_sources_is_warning_but_safe_for_baseline_replay(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")

    report = build_preflight(tmp_path, expected_history_parts=1)

    assert report["status"] == "PASS_WITH_WARNINGS"
    assert report["safe_to_replay"] is True
    assert report["warning_reasons"] == ["no_daily_packages_observed"]


def test_deep_source_test_catches_malformed_nonfirst_xml_member(tmp_path: Path) -> None:
    incoming, _archive = _dirs(tmp_path)
    _zip(incoming / "apc18840407-20251231-01.zip", "h1")
    daily = incoming / "apc260102.zip"
    with zipfile.ZipFile(daily, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a.xml", _xml("valid"))
        archive.writestr("b.xml", "<broken>")

    shallow = build_preflight(tmp_path, expected_history_parts=1, deep_source_test=False)
    deep = build_preflight(tmp_path, expected_history_parts=1, deep_source_test=True)

    assert shallow["status"] == "PASS"
    assert deep["status"] == "FAIL"
    assert "SOURCE_CONTAINER_OR_XML_UNREADABLE" in deep["hard_issue_types"]
