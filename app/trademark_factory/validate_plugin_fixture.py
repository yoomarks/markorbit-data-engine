from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.trademark_factory.mapping import MappingContract, MappingRule, SelectorKind
from app.trademark_factory.plugin import (
    JURISDICTION_PLUGIN_VERSION,
    JurisdictionPlugin,
    JurisdictionPluginRegistry,
    SourceAcquisitionBinding,
)
from app.trademark_factory.store_bundle import NativeStoreBundle, StoreBinding
from app.trademark_framework.acquisition import AcquisitionPage, AcquisitionPageRequest
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
from app.trademark_framework.native_store import NativeColumn, NativeSqlType, ObservationTableSpec
from app.trademark_framework.runtime import (
    FunctionalRuntimeAdapter,
    RuntimeRequest,
    RuntimeSourceKey,
)


_SOURCE_ID = "XX_PLUGIN_API"


@dataclass(frozen=True, slots=True)
class _Preflight:
    schema_valid: bool = True

    def as_dict(self) -> dict[str, object]:
        return {"schema_valid": self.schema_valid}


@dataclass(frozen=True, slots=True)
class _AcquisitionAdapter:
    adapter_id: str = "XX_PLUGIN_ACQUISITION_V1"

    def initial_cursor(self) -> str | None:
        return None

    def fetch_page(self, request: AcquisitionPageRequest) -> AcquisitionPage:
        return AcquisitionPage(
            page_key=f"page-{request.sequence}",
            payload=b'{"fixture":"plugin"}',
            next_cursor=None,
            media_type="application/json",
        )


def _pack() -> CountryPack:
    return CountryPack(
        jurisdiction="XX",
        aliases=("XZ",),
        store_schema="trademark_xx_plugin",
        maturity=JurisdictionStage.COUNTRY_STORE_READY,
        identity=IdentityContract(fields=("application_number",)),
        observation_domains=(ObservationDomain.RECORD,),
        current_projection=CurrentProjectionContract(
            mode=CurrentProjectionMode.NOT_IMPLEMENTED,
            notes="Virtual plugin fixture has no current projection.",
        ),
        asset_mode=AssetMode.NONE,
        sources=(
            SourceDescriptor(
                source_id=_SOURCE_ID,
                role=SourceRole.PRIMARY,
                authoritative=True,
                active_now=True,
                pipeline_ready=True,
                adapter_kind=SourceAdapterKind.REST_API,
                transport=TransportKind.HTTP_API,
                data_format=DataFormat.JSON,
                update_semantics=UpdateSemantics.API_CURRENT,
                pipeline_ids=("XX_PLUGIN_PIPELINE_V1",),
                parser_version="XX_PLUGIN_PARSER_V1",
                mapping_version="XX_PLUGIN_MAPPING_V1",
                preflight_profile="XX_PLUGIN_JSON",
                notes="Virtual source for plugin-registry validation only.",
            ),
        ),
        native_tables=("record_observation",),
    )


def _runtime_request_from_source(
    *,
    jurisdiction: str,
    source_id: str,
    path: Path,
    metadata: Mapping[str, object],
    max_records: int | None,
) -> RuntimeRequest:
    if jurisdiction.strip().upper() not in {"XX", "XZ"}:
        raise ValueError("virtual plugin runtime jurisdiction mismatch")
    if source_id != _SOURCE_ID:
        raise ValueError("virtual plugin runtime source mismatch")
    return RuntimeRequest(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        path=path,
        parser_version="XX_PLUGIN_PARSER_V1",
        metadata=dict(metadata),
        max_records=max_records,
    )


def _runtime_preflight(request: RuntimeRequest, sample_limit: int) -> _Preflight:
    assert request.source_id == _SOURCE_ID
    assert sample_limit > 0
    return _Preflight()


def _runtime_execute(request: RuntimeRequest) -> int:
    assert request.source_id == _SOURCE_ID
    return 1


def _runtime_adapter() -> FunctionalRuntimeAdapter:
    return FunctionalRuntimeAdapter(
        adapter_id="XX_PLUGIN_RUNTIME_V1",
        source_keys=(RuntimeSourceKey("XX", _SOURCE_ID),),
        _request_from_source=_runtime_request_from_source,
        _preflight=_runtime_preflight,
        _execute=_runtime_execute,
    )


def _store_bundle() -> NativeStoreBundle:
    mapping = MappingContract(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        version="XX_PLUGIN_MAPPING_V1",
        rules=(
            MappingRule(
                selector_kind=SelectorKind.JSON_POINTER,
                source_selector="/application_number",
                domain=ObservationDomain.RECORD,
                target_field="application_number",
                required=True,
            ),
        ),
    )
    return NativeStoreBundle(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        store_schema="trademark_xx_plugin",
        bindings=(
            StoreBinding(
                binding_id="record",
                spec=ObservationTableSpec(
                    schema_name="trademark_xx_plugin",
                    table_name="record_observation",
                    domain=ObservationDomain.RECORD,
                    native_columns=(
                        NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
                    ),
                ),
                contract=mapping,
            ),
        ),
    )


def main() -> int:
    plugin = JurisdictionPlugin(
        plugin_id="XX_TRADEMARK_PLUGIN_V1",
        pack=_pack(),
        runtime_adapters=(_runtime_adapter(),),
        acquisition_bindings=(
            SourceAcquisitionBinding(source_id=_SOURCE_ID, adapter=_AcquisitionAdapter()),
        ),
        store_bundles=(_store_bundle(),),
    )
    assert not plugin.validate(), plugin.validate()

    registry = JurisdictionPluginRegistry((plugin,))
    audit = registry.audit()
    assert audit.version == JURISDICTION_PLUGIN_VERSION
    assert audit.ready, audit.errors
    assert audit.plugin_count == 1
    assert audit.country_count == 1
    assert audit.runtime_adapter_count == 1
    assert audit.acquisition_adapter_count == 1
    assert audit.native_store_bundle_count == 1
    assert registry.country_pack("XZ").jurisdiction == "XX"

    runtime_registry = registry.runtime_registry()
    runtime_audit = runtime_registry.audit()
    assert runtime_audit.ready, runtime_audit.errors
    assert runtime_audit.adapter_count == 1
    assert runtime_audit.command_count == 0
    assert runtime_audit.source_key_count == 1
    runtime = runtime_registry.for_source("XX", _SOURCE_ID)
    request = runtime.request_from_source(
        jurisdiction="XX",
        source_id=_SOURCE_ID,
        path=Path("/tmp/virtual-plugin.json"),
        metadata={"fixture": True},
        max_records=5,
    )
    assert request.parser_version == "XX_PLUGIN_PARSER_V1"
    assert runtime.preflight(request).schema_valid is True
    assert runtime.execute(request) == 1

    acquisition = registry.acquisition_adapter("XZ", _SOURCE_ID)
    assert acquisition.adapter_id == "XX_PLUGIN_ACQUISITION_V1"
    assert acquisition.initial_cursor() is None
    bundle = registry.native_store_bundle("XZ", _SOURCE_ID)
    assert bundle.source_id == _SOURCE_ID
    assert bundle.store_schema == "trademark_xx_plugin"

    bad_runtime = FunctionalRuntimeAdapter(
        adapter_id="XX_BAD_RUNTIME_V1",
        source_keys=(RuntimeSourceKey("XX", "UNDECLARED_SOURCE"),),
        _request_from_source=_runtime_request_from_source,
        _preflight=_runtime_preflight,
        _execute=_runtime_execute,
    )
    invalid_plugin = JurisdictionPlugin(
        plugin_id="XX_BAD_PLUGIN_V1",
        pack=_pack(),
        runtime_adapters=(bad_runtime,),
    )
    assert any("not declared by CountryPack" in error for error in invalid_plugin.validate())

    print(
        {
            "status": "PASS",
            "plugin_version": JURISDICTION_PLUGIN_VERSION,
            "source_only_runtime_supported": True,
            "bespoke_cli_command_required": False,
            "country_alias_resolved": True,
            "acquisition_binding_resolved": True,
            "native_store_binding_resolved": True,
            "central_registry_mutated": False,
            "network_calls": False,
            "db_writes": False,
            "legal_conclusion": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
