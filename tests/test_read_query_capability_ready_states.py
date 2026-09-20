from app.read_query_capability import read_query_capability_contract

def _capability(capabilities, capability_id, jurisdiction):
    for item in capabilities:
        if item["id"] == capability_id and jurisdiction in item["jurisdictions"]:
            return item
    raise AssertionError((capability_id, jurisdiction))

def test_production_ready_capability_states_are_current():
    contract = read_query_capability_contract()
    assert contract["contract_version"] == "READ_QUERY_CAPABILITY_V2"
    caps = contract["capabilities"]
    assert _capability(caps, "exact_trademark_registration", "US")["state"] == "SUPPORTED_INDEXED"
    assert (
        _capability(caps, "agent_attorney_name_resolve", "US")["state"]
        == "IMPLEMENTED_REQUIRES_PRODUCTION_BACKFILL"
    )
    assert _capability(caps, "agent_attorney_name_resolve", "CN")["state"] == "SUPPORTED_INDEXED_EXACT_NAME"
    assert _capability(caps, "current_entity_portfolio", "CN")["state"] == "SUPPORTED_INDEXED"
    assert _capability(caps, "historical_entity_portfolio", "CN")["state"] == "SUPPORTED_INDEXED"
    assert _capability(caps, "trademark_event_timeline", "US")["state"] == "SUPPORTED_INDEXED"
    assert _capability(caps, "relationship_timeline", "CN")["state"] == "SUPPORTED_INDEXED"
    assert _capability(caps, "historical_entity_portfolio", "US")["state"] == "SUPPORTED_INDEXED"
