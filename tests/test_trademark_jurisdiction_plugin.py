from pathlib import Path

from app.trademark_factory.plugin import JurisdictionPlugin, JurisdictionPluginRegistry
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
from app.trademark_framework.runtime import (
    FunctionalRuntimeAdapter,
    RuntimeAdapterRegistry,
    RuntimeRequest,
    RuntimeSourceKey,
)


def _pack() -> CountryPack:
    return CountryPack(
        jurisdiction="XX",
        aliases=("XZ",),
        store_schema="trademark_xx",
        maturity=JurisdictionStage.SOURCE_PROFILED,
        identity=IdentityContract(fields=("application_number",)),
        observation_domains=(ObservationDomain.RECORD,),
        current_projection=CurrentProjectionContract(mode=CurrentProjectionMode.NOT_IMPLEMENTED),
        asset_mode=AssetMode.NONE,
        sources=(
            SourceDescriptor(
                source_id="XX_SOURCE",
                role=SourceRole.PRIMARY,
                authoritative=True,
                active_now=True,
                pipeline_ready=False,
                adapter_kind=SourceAdapterKind.REST_API,
                transport=TransportKind.HTTP_API,
                data_format=DataFormat.JSON,
                update_semantics=UpdateSemantics.API_CURRENT,
            ),
        ),
    )


def _request_from_source(**kwargs) -> RuntimeRequest:
    return RuntimeRequest(
        jurisdiction="XX",
        source_id="XX_SOURCE",
        path=Path(kwargs["path"]),
        parser_version="XX_PARSER_V1",
        metadata=kwargs["metadata"],
        max_records=kwargs["max_records"],
    )


class _Preflight:
    schema_valid = True

    def as_dict(self) -> dict[str, object]:
        return {"schema_valid": True}


def _preflight(request: RuntimeRequest, sample_limit: int) -> _Preflight:
    assert request.source_id == "XX_SOURCE"
    assert sample_limit > 0
    return _Preflight()


def _execute(request: RuntimeRequest) -> int:
    assert request.source_id == "XX_SOURCE"
    return 0


def _runtime(source_id: str = "XX_SOURCE") -> FunctionalRuntimeAdapter:
    return FunctionalRuntimeAdapter(
        adapter_id=f"RUNTIME_{source_id}",
        source_keys=(RuntimeSourceKey("XX", source_id),),
        _request_from_source=_request_from_source,
        _preflight=_preflight,
        _execute=_execute,
    )


def test_source_only_runtime_registry_does_not_require_bespoke_command() -> None:
    registry = RuntimeAdapterRegistry((_runtime(),))
    assert registry.audit().ready
    assert registry.audit().command_count == 0
    assert registry.audit().source_key_count == 1
    adapter = registry.for_source("XX", "XX_SOURCE")
    request = adapter.request_from_source(
        jurisdiction="XX",
        source_id="XX_SOURCE",
        path=Path("fixture.json"),
        metadata={},
        max_records=None,
    )
    assert request.parser_version == "XX_PARSER_V1"


def test_plugin_accepts_source_runtime_without_central_command() -> None:
    plugin = JurisdictionPlugin(
        plugin_id="XX_PLUGIN_V1",
        pack=_pack(),
        runtime_adapters=(_runtime(),),
    )
    assert not plugin.validate()
    registry = JurisdictionPluginRegistry((plugin,))
    assert registry.audit().ready
    assert registry.country_pack("XZ").jurisdiction == "XX"
    assert registry.runtime_registry().for_source("XX", "XX_SOURCE").adapter_id == "RUNTIME_XX_SOURCE"


def test_plugin_blocks_runtime_for_undeclared_source() -> None:
    plugin = JurisdictionPlugin(
        plugin_id="XX_PLUGIN_V1",
        pack=_pack(),
        runtime_adapters=(_runtime("UNDECLARED"),),
    )
    assert any("not declared by CountryPack" in error for error in plugin.validate())
