from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.trademark_framework.contracts import (
    AssetMode,
    CountryPack,
    CurrentProjectionMode,
    ObservationDomain,
    SourceAdapterKind,
    SourceDescriptor,
    SourceRole,
    TransportKind,
    UpdateSemantics,
)


CAPABILITY_MATRIX_VERSION = "TRADEMARK_SOURCE_CAPABILITY_MATRIX_V1"


class CapabilityState(StrEnum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class SourceCapability(StrEnum):
    FILE_SOURCE = "FILE_SOURCE"
    HTTP_API_SOURCE = "HTTP_API_SOURCE"
    SOAP_API_SOURCE = "SOAP_API_SOURCE"
    SFTP_SOURCE = "SFTP_SOURCE"
    BULK_SNAPSHOT = "BULK_SNAPSHOT"
    HISTORICAL_SEED = "HISTORICAL_SEED"
    INCREMENTAL_UPDATE = "INCREMENTAL_UPDATE"
    DELETE_EVENT = "DELETE_EVENT"
    RECORD_OBSERVATIONS = "RECORD_OBSERVATIONS"
    PARTY_OBSERVATIONS = "PARTY_OBSERVATIONS"
    GOODS_SERVICES = "GOODS_SERVICES"
    CLASSIFICATION = "CLASSIFICATION"
    EVENTS = "EVENTS"
    RELATIONSHIPS = "RELATIONSHIPS"
    ASSETS = "ASSETS"
    CURRENT_PROJECTION = "CURRENT_PROJECTION"
    MANIFEST_ORDERED_CURRENT = "MANIFEST_ORDERED_CURRENT"
    TOMBSTONE_CURRENT = "TOMBSTONE_CURRENT"
    PIPELINE_READY = "PIPELINE_READY"


_SOURCE_SCOPED_CAPABILITIES: tuple[SourceCapability, ...] = (
    SourceCapability.FILE_SOURCE,
    SourceCapability.HTTP_API_SOURCE,
    SourceCapability.SOAP_API_SOURCE,
    SourceCapability.SFTP_SOURCE,
    SourceCapability.BULK_SNAPSHOT,
    SourceCapability.HISTORICAL_SEED,
    SourceCapability.INCREMENTAL_UPDATE,
    SourceCapability.DELETE_EVENT,
    SourceCapability.PIPELINE_READY,
)


@dataclass(frozen=True, slots=True)
class SourceCapabilityReport:
    jurisdiction: str
    source_id: str
    states: tuple[tuple[SourceCapability, CapabilityState], ...]

    def state(self, capability: SourceCapability) -> CapabilityState:
        for key, value in self.states:
            if key == capability:
                return value
        raise KeyError(capability)

    def supports(self, capability: SourceCapability) -> bool:
        return self.state(capability) == CapabilityState.YES

    def as_dict(self) -> dict[str, object]:
        return {
            "jurisdiction": self.jurisdiction,
            "source_id": self.source_id,
            "capabilities": {key.value: value.value for key, value in self.states},
        }


@dataclass(frozen=True, slots=True)
class CountryCapabilityReport:
    jurisdiction: str
    states: tuple[tuple[SourceCapability, CapabilityState], ...]
    sources: tuple[SourceCapabilityReport, ...]

    def state(self, capability: SourceCapability) -> CapabilityState:
        for key, value in self.states:
            if key == capability:
                return value
        raise KeyError(capability)

    def supports(self, capability: SourceCapability) -> bool:
        return self.state(capability) == CapabilityState.YES

    def as_dict(self) -> dict[str, object]:
        return {
            "jurisdiction": self.jurisdiction,
            "capabilities": {key.value: value.value for key, value in self.states},
            "sources": [source.as_dict() for source in self.sources],
        }


def _yes_no(value: bool) -> CapabilityState:
    return CapabilityState.YES if value else CapabilityState.NO


def _source_state(source: SourceDescriptor, capability: SourceCapability) -> CapabilityState:
    if source.adapter_kind == SourceAdapterKind.UNRESOLVED:
        if capability == SourceCapability.PIPELINE_READY:
            return CapabilityState.NO
        return CapabilityState.UNKNOWN

    if capability == SourceCapability.FILE_SOURCE:
        return _yes_no(source.transport == TransportKind.FILE)
    if capability == SourceCapability.HTTP_API_SOURCE:
        return _yes_no(source.transport == TransportKind.HTTP_API)
    if capability == SourceCapability.SOAP_API_SOURCE:
        return _yes_no(source.transport == TransportKind.SOAP_API)
    if capability == SourceCapability.SFTP_SOURCE:
        return _yes_no(source.transport == TransportKind.SFTP)
    if capability == SourceCapability.BULK_SNAPSHOT:
        return _yes_no(source.update_semantics == UpdateSemantics.SNAPSHOT)
    if capability == SourceCapability.HISTORICAL_SEED:
        return _yes_no(source.update_semantics == UpdateSemantics.HISTORICAL_SEED)
    if capability == SourceCapability.INCREMENTAL_UPDATE:
        return _yes_no(
            source.role == SourceRole.INCREMENTAL
            or source.update_semantics
            in {
                UpdateSemantics.APPEND_ONLY,
                UpdateSemantics.UPDATE_DELETE,
                UpdateSemantics.API_CURRENT,
                UpdateSemantics.UPSERT_CURRENT,
            }
        )
    if capability == SourceCapability.DELETE_EVENT:
        return _yes_no(source.update_semantics == UpdateSemantics.UPDATE_DELETE)
    if capability == SourceCapability.PIPELINE_READY:
        return _yes_no(source.pipeline_ready)
    raise ValueError(f"capability is not source-scoped: {capability.value}")


def derive_source_capabilities(
    jurisdiction: str,
    source: SourceDescriptor,
) -> SourceCapabilityReport:
    return SourceCapabilityReport(
        jurisdiction=jurisdiction,
        source_id=source.source_id,
        states=tuple(
            (capability, _source_state(source, capability))
            for capability in _SOURCE_SCOPED_CAPABILITIES
        ),
    )


def _aggregate_source_state(
    reports: tuple[SourceCapabilityReport, ...],
    capability: SourceCapability,
) -> CapabilityState:
    states = {report.state(capability) for report in reports}
    if CapabilityState.YES in states:
        return CapabilityState.YES
    if CapabilityState.UNKNOWN in states:
        return CapabilityState.UNKNOWN
    return CapabilityState.NO


def _asset_state(pack: CountryPack) -> CapabilityState:
    if pack.asset_mode in {AssetMode.EXISTING_SUBSYSTEM, AssetMode.OBJECT_STORE}:
        return CapabilityState.YES
    if pack.asset_mode == AssetMode.NONE:
        return CapabilityState.NO
    return CapabilityState.UNKNOWN


def derive_country_capabilities(pack: CountryPack) -> CountryCapabilityReport:
    """Derive only capabilities explicitly supported by a ``CountryPack``.

    Source-level transport/update states are first derived per SourceDescriptor and then
    conservatively aggregated. Country-level observation/current/asset capabilities come
    from the pack itself. The matrix intentionally avoids unproven claims such as owner
    history completeness or API pagination semantics.
    """
    source_reports = tuple(
        derive_source_capabilities(pack.jurisdiction, source) for source in pack.sources
    )
    domains = set(pack.observation_domains)
    current_implemented = pack.current_projection.mode not in {
        CurrentProjectionMode.NOT_IMPLEMENTED,
        CurrentProjectionMode.HISTORICAL_ONLY,
    }
    manifest_ordered = pack.current_projection.mode == CurrentProjectionMode.MANIFEST_ORDERED

    states: dict[SourceCapability, CapabilityState] = {
        capability: _aggregate_source_state(source_reports, capability)
        for capability in _SOURCE_SCOPED_CAPABILITIES
    }
    states.update(
        {
            SourceCapability.RECORD_OBSERVATIONS: _yes_no(ObservationDomain.RECORD in domains),
            SourceCapability.PARTY_OBSERVATIONS: _yes_no(ObservationDomain.PARTY in domains),
            SourceCapability.GOODS_SERVICES: _yes_no(ObservationDomain.GOODS_SERVICE in domains),
            SourceCapability.CLASSIFICATION: _yes_no(ObservationDomain.CLASSIFICATION in domains),
            SourceCapability.EVENTS: _yes_no(ObservationDomain.EVENT in domains),
            SourceCapability.RELATIONSHIPS: _yes_no(ObservationDomain.RELATIONSHIP in domains),
            SourceCapability.ASSETS: _asset_state(pack),
            SourceCapability.CURRENT_PROJECTION: _yes_no(current_implemented),
            SourceCapability.MANIFEST_ORDERED_CURRENT: _yes_no(manifest_ordered),
            SourceCapability.TOMBSTONE_CURRENT: _yes_no(
                current_implemented and pack.current_projection.tombstone_supported
            ),
        }
    )

    return CountryCapabilityReport(
        jurisdiction=pack.jurisdiction,
        states=tuple((capability, states[capability]) for capability in SourceCapability),
        sources=source_reports,
    )


def capability_matrix(packs: tuple[CountryPack, ...]) -> tuple[CountryCapabilityReport, ...]:
    return tuple(derive_country_capabilities(pack) for pack in packs)
