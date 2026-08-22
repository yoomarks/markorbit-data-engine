from __future__ import annotations

import ast

from app.trademark_factory import COUNTRY_FACTORY_VERSION
from app.trademark_factory.audit import audit_country_factory
from app.trademark_factory.capabilities import (
    CAPABILITY_MATRIX_VERSION,
    CapabilityState,
    SourceCapability,
    derive_country_capabilities,
)
from app.trademark_factory.mapping import (
    MAPPING_CONTRACT_VERSION,
    MappingContract,
    MappingRule,
    SelectorKind,
    extract_declared_value,
)
from app.trademark_factory.readiness import readiness_report
from app.trademark_factory.registry import FACTORY_REGISTRY_VERSION, FactoryRegistry, factory_registry
from app.trademark_factory.scaffold import build_country_scaffold
from app.trademark_framework.contracts import (
    AssetMode,
    CountryPack,
    CurrentProjectionContract,
    CurrentProjectionMode,
    DataFormat,
    IdentityContract,
    JurisdictionStage,
    ObservationDomain,
    SourceAdapterKind,
    SourceDescriptor,
    SourceRole,
    TransportKind,
    UpdateSemantics,
)
from app.trademark_framework.scaffold import SCAFFOLD_VERSION


def _virtual_pack() -> CountryPack:
    return CountryPack(
        jurisdiction="XX",
        aliases=("XZ",),
        store_schema="trademark_xx",
        maturity=JurisdictionStage.CURRENT_PROJECTION_READY,
        identity=IdentityContract(fields=("application_number",)),
        observation_domains=(
            ObservationDomain.RECORD,
            ObservationDomain.PARTY,
            ObservationDomain.GOODS_SERVICE,
            ObservationDomain.EVENT,
            ObservationDomain.RELATIONSHIP,
            ObservationDomain.ASSET,
            ObservationDomain.SOURCE_OPERATION,
        ),
        current_projection=CurrentProjectionContract(
            mode=CurrentProjectionMode.MANIFEST_ORDERED,
            ordering_fields=("source_period_end", "source_precedence", "source_sequence"),
            tombstone_supported=True,
            notes="Virtual fixture only: proves reusable ordering contract shape.",
        ),
        asset_mode=AssetMode.OBJECT_STORE,
        sources=(
            SourceDescriptor(
                source_id="XX_OFFICIAL_API",
                role=SourceRole.PRIMARY,
                authoritative=True,
                active_now=True,
                pipeline_ready=True,
                adapter_kind=SourceAdapterKind.REST_API,
                transport=TransportKind.HTTP_API,
                data_format=DataFormat.JSON,
                update_semantics=UpdateSemantics.UPDATE_DELETE,
                pipeline_ids=("XX_OFFICIAL_API_V1",),
                parser_version="XX_PARSER_V1",
                mapping_version="XX_MAPPING_V1",
                preflight_profile="XX_API_JSON",
                notes="Deterministic virtual source; never a real jurisdiction/source claim.",
            ),
        ),
        native_tables=(
            "record_observation",
            "party_observation",
            "goods_service_observation",
            "event_observation",
            "relationship_observation",
            "asset_observation",
            "record_state",
        ),
        notes="Virtual country used only to validate country-factory reuse.",
    )


def _mapping_contract() -> MappingContract:
    return MappingContract(
        jurisdiction="XX",
        source_id="XX_OFFICIAL_API",
        version="XX_MAPPING_V1",
        identity_targets=("application_number",),
        rules=(
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/application/id",
                domain=ObservationDomain.RECORD,
                target_field="application_number",
                required=True,
            ),
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/mark/text",
                domain=ObservationDomain.RECORD,
                target_field="mark_text",
            ),
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/owner/name",
                domain=ObservationDomain.PARTY,
                target_field="party_name",
                required=True,
            ),
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/goods/0/text",
                domain=ObservationDomain.GOODS_SERVICE,
                target_field="text_value",
                repeated=False,
            ),
        ),
        notes="Virtual declarative JSON mapping fixture.",
    )


def main() -> int:
    production_registry = factory_registry()
    production_audit = production_registry.audit()
    assert production_audit.version == FACTORY_REGISTRY_VERSION
    assert production_audit.ready, production_audit.errors
    assert production_audit.country_count == 6
    assert production_audit.source_count == 17

    factory_audit = audit_country_factory(production_registry)
    assert factory_audit.factory_version == COUNTRY_FACTORY_VERSION
    assert factory_audit.capability_matrix_version == CAPABILITY_MATRIX_VERSION
    assert factory_audit.scaffold_version == SCAFFOLD_VERSION
    assert factory_audit.ready, factory_audit.errors

    ca = production_registry.country_pack("CA")
    ca_capabilities = derive_country_capabilities(ca)
    assert ca_capabilities.state(SourceCapability.BULK_SNAPSHOT) == CapabilityState.YES
    assert ca_capabilities.state(SourceCapability.INCREMENTAL_UPDATE) == CapabilityState.YES
    assert ca_capabilities.state(SourceCapability.DELETE_EVENT) == CapabilityState.YES
    assert ca_capabilities.state(SourceCapability.GOODS_SERVICES) == CapabilityState.YES
    assert ca_capabilities.state(SourceCapability.MANIFEST_ORDERED_CURRENT) == CapabilityState.YES
    assert ca_capabilities.state(SourceCapability.ASSETS) == CapabilityState.UNKNOWN

    au = production_registry.country_pack("AU")
    au_capabilities = derive_country_capabilities(au)
    assert au_capabilities.state(SourceCapability.FILE_SOURCE) == CapabilityState.YES
    assert au_capabilities.state(SourceCapability.BULK_SNAPSHOT) == CapabilityState.YES
    assert au_capabilities.state(SourceCapability.RELATIONSHIPS) == CapabilityState.YES
    assert au_capabilities.state(SourceCapability.CURRENT_PROJECTION) == CapabilityState.NO

    gb = production_registry.country_pack("UK")
    gb_capabilities = derive_country_capabilities(gb)
    assert gb_capabilities.state(SourceCapability.FILE_SOURCE) == CapabilityState.YES
    assert gb_capabilities.state(SourceCapability.HTTP_API_SOURCE) == CapabilityState.UNKNOWN

    virtual = _virtual_pack()
    virtual_registry = FactoryRegistry((virtual,))
    virtual_audit = virtual_registry.audit()
    assert virtual_audit.ready, virtual_audit.errors
    assert virtual_registry.country_pack("XZ").jurisdiction == "XX"
    assert virtual_registry.profile("XX").source("XX_OFFICIAL_API").pipeline_ready is True

    virtual_readiness = readiness_report(virtual)
    assert not virtual_readiness.structural_warnings
    assert virtual_readiness.declared_stage == JurisdictionStage.CURRENT_PROJECTION_READY

    virtual_capabilities = derive_country_capabilities(virtual)
    for capability in (
        SourceCapability.HTTP_API_SOURCE,
        SourceCapability.INCREMENTAL_UPDATE,
        SourceCapability.DELETE_EVENT,
        SourceCapability.PARTY_OBSERVATIONS,
        SourceCapability.GOODS_SERVICES,
        SourceCapability.EVENTS,
        SourceCapability.RELATIONSHIPS,
        SourceCapability.ASSETS,
        SourceCapability.CURRENT_PROJECTION,
        SourceCapability.MANIFEST_ORDERED_CURRENT,
        SourceCapability.TOMBSTONE_CURRENT,
        SourceCapability.PIPELINE_READY,
    ):
        assert virtual_capabilities.supports(capability), capability

    mapping = _mapping_contract()
    assert MAPPING_CONTRACT_VERSION in mapping.as_dict()["contract_version"]
    assert not mapping.validate(), mapping.validate()
    assert not mapping.validate_against(virtual), mapping.validate_against(virtual)

    payload = {
        "application": {"id": "XX-123"},
        "mark": {"text": "VIRTUAL MARK"},
        "owner": {"name": "Example Owner"},
        "goods": [{"text": "virtual software"}],
    }
    extracted = {
        rule.target_field: extract_declared_value(payload, rule) for rule in mapping.rules
    }
    assert extracted == {
        "application_number": "XX-123",
        "mark_text": "VIRTUAL MARK",
        "party_name": "Example Owner",
        "text_value": "virtual software",
    }

    http_plan = build_country_scaffold(
        jurisdiction="KR",
        source_id="KIPO_EXAMPLE_API",
        adapter_kind=SourceAdapterKind.REST_API,
        data_format=DataFormat.JSON,
        update_semantics=UpdateSemantics.API_CURRENT,
        transport=TransportKind.HTTP_API,
    )
    assert http_plan.version == "TRADEMARK_COUNTRY_SCAFFOLD_V4"
    http_acquisition = http_plan.files["app/trademark_jurisdictions/kr/acquisition.py"]
    assert "HttpPaginatedAcquisitionAdapter" in http_acquisition
    assert "PageNumberPagination" in http_acquisition
    assert "OffsetLimitPagination" in http_acquisition
    assert "OpaqueCursorPagination" in http_acquisition
    assert "runtime_headers" in http_acquisition
    assert "NotImplementedError" in http_acquisition

    file_plan = build_country_scaffold(
        jurisdiction="JP",
        source_id="JPO_EXAMPLE_BULK",
        adapter_kind=SourceAdapterKind.ZIP_XML,
        data_format=DataFormat.XML,
        update_semantics=UpdateSemantics.SNAPSHOT,
        transport=TransportKind.FILE,
    )
    file_acquisition = file_plan.files["app/trademark_jurisdictions/jp/acquisition.py"]
    assert "AcquisitionPageRequest" in file_acquisition
    assert "HttpPaginatedAcquisitionAdapter" not in file_acquisition

    for plan in (http_plan, file_plan):
        for relative_path, content in plan.files.items():
            if relative_path.endswith(".py"):
                ast.parse(content, filename=relative_path)
        country_source = plan.files[
            f"app/trademark_jurisdictions/{plan.request.jurisdiction.lower()}/country.py"
        ]
        assert "pipeline_ready=False" in country_source
        assert "TODO_SOURCE_IDENTITY" in country_source

    print(
        {
            "status": "PASS",
            "factory_version": COUNTRY_FACTORY_VERSION,
            "framework_country_count": production_audit.country_count,
            "framework_source_count": production_audit.source_count,
            "capability_matrix_version": CAPABILITY_MATRIX_VERSION,
            "mapping_contract_version": MAPPING_CONTRACT_VERSION,
            "scaffold_version": SCAFFOLD_VERSION,
            "virtual_country": "XX",
            "virtual_registry_ready": virtual_audit.ready,
            "virtual_mapping_valid": True,
            "http_scaffold_uses_shared_acquisition": True,
            "file_scaffold_preserved": True,
            "production_registry_mutated": False,
            "db_writes": False,
            "network_calls": False,
            "legal_conclusion": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
