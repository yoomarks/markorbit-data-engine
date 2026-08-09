from datetime import date

from app.us.package_meta import (
    DAILY_RANK_MAJOR,
    HISTORY_RANK_MAJOR,
    infer_us_package_descriptor,
)


def test_historical_application_package_descriptor() -> None:
    descriptor = infer_us_package_descriptor("apc18840407-20251231-05.zip")
    assert descriptor.package_kind == "HISTORICAL_APPLICATIONS"
    assert descriptor.partition_dimension == "COVERAGE_RANGE_PART"
    assert descriptor.partition_value == "1884-04-07/2025-12-31#005"
    assert descriptor.source_period_start == date(1884, 4, 7)
    assert descriptor.source_period_end == date(2025, 12, 31)
    assert descriptor.source_sequence == 20251231005


def test_daily_application_package_descriptor() -> None:
    descriptor = infer_us_package_descriptor("apc260809.zip")
    assert descriptor.package_kind == "DAILY_APPLICATIONS"
    assert descriptor.partition_dimension == "UPDATE_DATE"
    assert descriptor.partition_value == "2026-08-09"
    assert descriptor.source_period_start == date(2026, 8, 9)
    assert descriptor.source_period_end == date(2026, 8, 9)
    assert descriptor.source_sequence == 20260809


def test_history_is_always_lower_precedence_than_daily() -> None:
    history = infer_us_package_descriptor("apc18840407-20251231-05.zip").source_rank(999999)
    daily = infer_us_package_descriptor("apc260101.zip").source_rank(1)
    assert history < daily
    assert history >= HISTORY_RANK_MAJOR
    assert daily >= DAILY_RANK_MAJOR


def test_daily_package_rank_is_chronological_and_deterministic() -> None:
    earlier = infer_us_package_descriptor("apc260808.zip").source_rank(4)
    later = infer_us_package_descriptor("apc260809.zip").source_rank(1)
    assert earlier < later
    assert later == DAILY_RANK_MAJOR + 20260809 * 1_000_000 + 1


def test_unknown_or_invalid_package_name_has_no_precedence() -> None:
    assert infer_us_package_descriptor("mystery.zip").package_kind == "UNKNOWN"
    assert infer_us_package_descriptor("apc260231.zip").package_kind == "UNKNOWN"
    assert (
        infer_us_package_descriptor("apc20251231-18840407-05.zip").package_kind
        == "UNKNOWN"
    )


def test_two_digit_year_pivot_is_fixed_not_clock_dependent() -> None:
    assert infer_us_package_descriptor("apc991231.zip").partition_value == "1999-12-31"
    assert infer_us_package_descriptor("apc000101.zip").partition_value == "2000-01-01"
