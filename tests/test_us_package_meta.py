from datetime import date

from app.us.package_meta import DAILY_RANK_MAJOR, infer_us_package_descriptor


def test_daily_application_package_descriptor() -> None:
    descriptor = infer_us_package_descriptor("apc260809.zip")
    assert descriptor.package_kind == "DAILY_APPLICATIONS"
    assert descriptor.partition_dimension == "UPDATE_DATE"
    assert descriptor.partition_value == "2026-08-09"
    assert descriptor.source_period_start == date(2026, 8, 9)
    assert descriptor.source_period_end == date(2026, 8, 9)
    assert descriptor.source_sequence == 20260809


def test_daily_package_rank_is_chronological_and_deterministic() -> None:
    earlier = infer_us_package_descriptor("apc260808.zip").source_rank(4)
    later = infer_us_package_descriptor("apc260809.zip").source_rank(1)
    assert earlier < later
    assert later == DAILY_RANK_MAJOR + 20260809 * 1_000_000 + 1


def test_unknown_or_invalid_package_name_has_no_precedence() -> None:
    assert infer_us_package_descriptor("mystery.zip").package_kind == "UNKNOWN"
    assert infer_us_package_descriptor("apc260231.zip").package_kind == "UNKNOWN"


def test_two_digit_year_pivot_is_fixed_not_clock_dependent() -> None:
    assert infer_us_package_descriptor("apc991231.zip").partition_value == "1999-12-31"
    assert infer_us_package_descriptor("apc000101.zip").partition_value == "2000-01-01"
