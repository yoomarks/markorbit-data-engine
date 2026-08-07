from datetime import date

from app.cn.text import parse_date


def test_parse_date_supports_pre_1970_history():
    assert parse_date("1950-01-01") == date(1950, 1, 1)


def test_parse_date_rejects_outside_date32_range():
    assert parse_date("1899-12-31") is None
    assert parse_date("2300-01-01") is None
