from __future__ import annotations

import pytest

from app.us.audit_real_data_v2 import augment_report, historical_part_completeness


def _history(part: int, *, coverage_end: str = "2025-12-31") -> dict:
    return {
        "package_id": f"00000000-0000-0000-0000-{part:012d}",
        "file_name": f"apc18840407-{coverage_end.replace('-', '')}-{part:02d}.zip",
        "package_kind": "HISTORICAL_APPLICATIONS",
        "partition_value": f"1884-04-07/{coverage_end}#{part:03d}",
        "status": "SUCCESS",
    }


def _base_report() -> dict:
    return {
        "status": "PASS",
        "hard_fail_reasons": [],
        "not_ready_reasons": [],
        "warning_reasons": [],
    }


def test_history_parts_are_complete_only_when_01_through_expected_tail_exist() -> None:
    result = historical_part_completeness(
        [_history(part) for part in range(1, 6)],
        expected_history_parts=5,
    )
    assert result["complete"] is True
    assert result["baseline_coverage"]["observed_parts"] == [1, 2, 3, 4, 5]
    assert result["baseline_coverage"]["numbering_start"] == 1
    assert result["missing_expected_parts"] == []
    assert result["unexpected_parts"] == []


def test_missing_part_01_is_detected_as_leading_gap() -> None:
    result = historical_part_completeness(
        [_history(2), _history(3), _history(4)],
        expected_history_parts=4,
    )
    assert result["complete"] is False
    assert result["baseline_coverage"]["missing_through_observed_max"] == [1]
    assert result["missing_expected_parts"] == [1]
    assert result["leading_or_interior_gap"] is True


def test_interior_gap_is_detected() -> None:
    result = historical_part_completeness(
        [_history(1), _history(3), _history(4)],
        expected_history_parts=4,
    )
    assert result["complete"] is False
    assert result["baseline_coverage"]["missing_through_observed_max"] == [2]
    assert result["missing_expected_parts"] == [2]


def test_observed_parts_beyond_pinned_tail_are_not_accepted() -> None:
    result = historical_part_completeness(
        [_history(1), _history(2), _history(3), _history(4)],
        expected_history_parts=3,
    )
    assert result["complete"] is False
    assert result["missing_expected_parts"] == []
    assert result["unexpected_parts"] == [4]


def test_part_zero_is_invalid_instead_of_becoming_alternate_numbering_scheme() -> None:
    result = historical_part_completeness(
        [_history(0), _history(1)],
        expected_history_parts=1,
    )
    assert result["complete"] is False
    assert result["invalid_partition_count"] == 1
    assert result["invalid_partitions"][0]["reason"] == "invalid_coverage_range_or_part_number"


def test_unpinned_tail_is_not_ready_not_pass_with_warning() -> None:
    report = augment_report(
        _base_report(),
        [_history(1), _history(2), _history(3)],
        expected_history_parts=None,
    )
    assert report["status"] == "NOT_READY"
    assert "historical_tail_part_count_not_pinned" in report["not_ready_reasons"]
    assert report["historical_part_completeness"]["complete"] is False


def test_missing_or_extra_pinned_parts_are_not_ready() -> None:
    missing_report = augment_report(
        _base_report(),
        [_history(1), _history(3)],
        expected_history_parts=3,
    )
    assert missing_report["status"] == "NOT_READY"
    assert "historical_part_sequence_incomplete" in missing_report["not_ready_reasons"]
    assert "expected_historical_parts_missing" in missing_report["not_ready_reasons"]

    extra_report = augment_report(
        _base_report(),
        [_history(1), _history(2), _history(3), _history(4)],
        expected_history_parts=3,
    )
    assert extra_report["status"] == "NOT_READY"
    assert "historical_parts_exceed_expected_count" in extra_report["not_ready_reasons"]


def test_expected_history_parts_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        historical_part_completeness([_history(1)], expected_history_parts=0)
