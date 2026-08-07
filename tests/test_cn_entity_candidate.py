from app.cn.entity import build_entity_candidate


def test_exact_name_address_creates_entity_candidate():
    item = build_entity_candidate(
        role="OWNER",
        raw_name="南宁新富农畜牧有限责任公司",
        raw_address="广西南宁市某路1号",
        country_code="CN",
        region_code="CN-GX",
        city="南宁市",
    )
    assert item is not None
    assert item.entity_type == "TRADEMARK_PARTY"
    assert item.resolution_method == "EXACT_NAME_ADDRESS"


def test_owner_without_address_remains_unresolved():
    item = build_entity_candidate(
        role="OWNER",
        raw_name="某某公司",
        raw_address="",
        country_code="CN",
        region_code="",
        city="",
    )
    assert item is None


def test_agent_code_is_safe_exact_key():
    item = build_entity_candidate(
        role="AGENT",
        raw_name="某商标代理有限公司",
        raw_address="",
        country_code="CN",
        region_code="",
        city="",
        agent_code="100001",
    )
    assert item is not None
    assert item.entity_type == "AGENT_FIRM"
    assert item.resolution_method == "EXACT_AGENT_CODE"
