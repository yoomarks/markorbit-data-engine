from app.cn.status import GOODS_STATUS_MAPPING_VERSION, classify_goods_status


def test_blank_status_has_no_negative_signal():
    result = classify_goods_status("")
    assert result.bucket == "ACTIVE"
    assert result.semantic == "NO_NEGATIVE_SIGNAL"
    assert result.source_finality == "OPEN"
    assert result.operational_effect == "EFFECTIVE_UNLESS_CONTRADICTED"


def test_code_0_is_reversible_risk_not_final_inactive():
    result = classify_goods_status("0")
    assert result.raw == "0"
    assert result.bucket == "ACTIVE"
    assert result.semantic == "REVERSIBLE_OR_UNRESOLVED_RISK"
    assert result.source_finality == "REVERSIBLE"
    assert result.operational_effect == "EFFECTIVE_AT_RISK"
    assert result.mapping_version == GOODS_STATUS_MAPPING_VERSION


def test_code_1_is_item_inactive_high_confidence_without_cause():
    result = classify_goods_status("1")
    assert result.raw == "1"
    assert result.bucket == "INACTIVE"
    assert result.semantic == "INACTIVE_HIGH_CONFIDENCE"
    assert result.source_finality == "SOURCE_NOT_FINALIZED"
    assert result.operational_effect == "INACTIVE_HIGH_CONFIDENCE"
    assert "NONRENEW" not in result.reason


def test_code_2_is_final_item_inactive_without_cause():
    result = classify_goods_status("2")
    assert result.raw == "2"
    assert result.bucket == "INACTIVE"
    assert result.semantic == "FINAL_INACTIVE"
    assert result.source_finality == "FINAL"
    assert result.operational_effect == "INACTIVE_CONFIRMED"
    assert "REFUS" not in result.reason
    assert "CANCEL" not in result.reason


def test_other_numeric_status_remains_unknown():
    result = classify_goods_status("9")
    assert result.bucket == "UNKNOWN"
    assert result.semantic == "UNKNOWN"
    assert result.reason == "UNMAPPED_NUMERIC_CODE"


def test_explicit_inactive_text_is_recognized():
    assert classify_goods_status("删除").bucket == "INACTIVE"
    assert classify_goods_status("无效").operational_effect == "INACTIVE_CONFIRMED"
