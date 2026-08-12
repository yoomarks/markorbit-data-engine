from app.component_versions import component_versions
from app.integration_api import integration_contract


def test_integration_contract_exposes_authoritative_component_matrix() -> None:
    contract = integration_contract()
    assert contract["component_versions"] == component_versions()
    assert contract["component_versions"]["components"]["us_application"]["schema_version"] == "US_M1.4"
