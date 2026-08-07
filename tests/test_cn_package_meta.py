from datetime import date

from app.cn.package_meta import infer_package_descriptor


def test_year_file_is_filing_year_partition_not_snapshot():
    item = infer_package_descriptor("1999.zip")
    assert item.package_kind == "BASE_PARTITION"
    assert item.partition_dimension == "FILING_YEAR"
    assert item.partition_value == "1999"
    assert item.source_period_end is None


def test_year_month_file_is_monthly_patch():
    item = infer_package_descriptor("2023_1.zip")
    assert item.package_kind == "MONTHLY_PATCH"
    assert item.partition_dimension == "UPDATE_MONTH"
    assert item.partition_value == "2023-01"
    assert item.source_period_start == date(2023, 1, 1)
    assert item.source_period_end == date(2023, 1, 31)


def test_monthly_patch_always_outranks_base_partition():
    base = infer_package_descriptor("2000.zip")
    monthly = infer_package_descriptor("2023_1.zip")
    assert monthly.source_rank(1) > base.source_rank(999999)
