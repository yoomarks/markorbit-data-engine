from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.trademark_framework.contracts import (
    AssetMode,
    CountryPack,
    CurrentProjectionMode,
    ObservationDomain,
    SourceAdapterKind,
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


@dataclass(frozen=True, slots=True)
class CountryCapabilityReport:
    jurisdiction: str
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
            "capabilities": {key.value: value.value for key, value in self.states},
        }


def _yes_no(value: bool) -> CapabilityState:
    return CapabilityState.YES if value else CapabilityState.NO


def _transport_state(pack: CountryPack, transport: TransportKind) -> CapabilityState:
    if any(source.transport == transport for source in pack.sources):
        return CapabilityState.YES
    if any(source.transport == TransportKind.UNRESOLVED for source in pack.sources):
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

    The matrix intentionally avoids claims such as owner-history completeness or API
    pagination unless the framework has a contract that proves them. ``UNKNOWN`` is used
    whenever an unresolved future source/asset contract could change the answer.
    """
    semantics = {source.update_semantics for source in pack.sources}
    roles_incremental = any(source.role.value == "INCREMENTAL" for source in pack.sources)
    domains = set(pack.observation_domains)

    current_implemented = pack.current_projection.mode not in {
        CurrentProjectionMode.NOT_IMPLEMENTED,
        CurrentProjectionMode.HISTORICAL_ONLY,
    }
    manifest_ordered = pack.current_projection.mode == CurrentProjectionMode.MANIFEST_ORDERED

    states = {
        SourceCapability.FILE_SOURCE: _transport_state(pack, TransportKind.FILE),
        SourceCapability.HTTP_API_SOURCE: _transport_state(pack, TransportKind.HTTP_API),
        SourceCapability.SOAP_API_SOURCE: _transport_state(pack, TransportKind.SOAP_API),
        SourceCapability.SFTP_SOURCE: _transport_state(pack, TransportKind.SFTP),
        SourceCapability.BULK_SNAPSHOT: _yes_no(UpdateSemantics.SNAPSHOT in semantics),
        SourceCapability.HISTORICAL_SEED: _yes_no(UpdateSemantics.HISTORICAL_SEED in semantics),
        SourceCapability.INCREMENTAL_UPDATE: _yes_no(
            roles_incremental
            or bool(
                semantics
                & {
                    UpdateSemantics.APPEND_ONLY,
                    UpdateSemantics.UPDATE_DELETE,
                    UpdateSemantics.API_CURRENT,
                    UpdateSemantics.UPSERT_CURRENT,
                }
            )
        ),
        SourceCapability.DELETE_EVENT: _yes_no(UpdateSemantics.UPDATE_DELETE in semantics),
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
        SourceCapability.PIPELINE_READY: _yes_no(any(source.pipeline_ready for source in pack.sources)),
    }

    # An unresolved source means the country may later gain a transport/update capability,
    # but it must not erase explicit facts already proven by implemented sources.
    unresolved = any(
        source.adapter_kind == SourceAdapterKind.UNRESOLVED for source in pack.sources
    )
    if unresolved:
        for capability in (
            SourceCapability.BULK_SNAPSHOT,
            SourceCapability.INCREMENTAL_UPDATE,
            SourceCapability.DELETE_EVENT,
        ):
            if states[capability] == CapabilityState.NO:
                states[capability] = CapabilityState.UNKNOWN

    return CountryCapabilityReport(
        jurisdiction=pack.jurisdiction,
        states=tuple((capability, states[capability]) for capability in SourceCapability),
    )


def capability_matrix(packs: tuple[CountryPack, ...]) -> tuple[CountryCapabilityReport, ...]:
    return tuple(derive_country_capabilities(pack) for pack in packs)
