from app.cn.status import GOODS_STATUS_MAPPING_VERSION, classify_goods_status


def test_blank_status_is_pipeline_default_active():
    result = classify_goods_status("")
    assert result.bucket == "ACTIVE"
    assert result.reason == "BLANK_DEFAULT_ACTIVE"


def test_numeric_status_is_preserved_without_guessing():
    for code in ("0", "1", "2"):
        result = classify_goods_status(code)
        assert result.raw == code
        assert result.bucket == "UNKNOWN"
        assert result.reason == "UNMAPPED_NUMERIC_CODE"
        assert result.mapping_version == GOODS_STATUS_MAPPING_VERSION


def test_explicit_inactive_text_is_recognized():
    assert classify_goods_status("删除").bucket == "INACTIVE"
    assert classify_goods_status("无效").bucket == "INACTIVE"
