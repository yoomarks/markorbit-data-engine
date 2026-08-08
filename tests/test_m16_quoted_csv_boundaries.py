from app.cn import reader
from app.cn import ingest_m16  # noqa: F401  # installs M1.6 CSV-aware boundary probe
from app.cn.schema import SCHEMA_BY_ROLE


def test_basic_boundary_accepts_fully_quoted_prefix() -> None:
    schema = SCHEMA_BY_ROLE["basic"]
    line = '"12345678","35","2025-11-01","MARK","普通"'
    assert reader._record_start(schema, line)


def test_applicant_boundary_accepts_fully_quoted_prefix() -> None:
    schema = SCHEMA_BY_ROLE["applicant"]
    line = '"12345678","35","申请人有限公司","","北京市",""'
    assert reader._record_start(schema, line)


def test_goods_boundary_accepts_fully_quoted_prefix() -> None:
    schema = SCHEMA_BY_ROLE["goods"]
    line = '"12345678","35","3501","1","广告",""'
    assert reader._record_start(schema, line)


def test_boundary_still_rejects_invalid_application_number() -> None:
    schema = SCHEMA_BY_ROLE["goods"]
    line = '"NOT-A-CASE","35","3501","1","广告",""'
    assert not reader._record_start(schema, line)
