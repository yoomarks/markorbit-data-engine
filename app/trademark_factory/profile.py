from __future__ import annotations

from dataclasses import dataclass

from app.trademark_framework.contracts import (
    AssetMode,
    CountryPack,
    CurrentProjectionMode,
    DataFormat,
    JurisdictionStage,
    ObservationDomain,
    SourceAdapterKind,
    SourceDescriptor,
    SourceRole,
    TransportKind,
    UpdateSemantics,
)


@dataclass(frozen=True, slots=True)
class SourceProfile:
    """Read-only factory projection of a framework ``SourceDescriptor``.

    The jurisdiction framework remains the source of truth. The factory consumes its
    descriptors to automate onboarding/auditing; it does not maintain a second source
    catalog or infer authority/legal semantics that the descriptor does not declare.
    """

    source_id: str
    role: SourceRole
    authoritative: bool
    active_now: bool
    pipeline_ready: bool
    adapter_kind: SourceAdapterKind
    transport: TransportKind
    data_format: DataFormat
    update_semantics: UpdateSemantics
    pipeline_ids: tuple[str, ...]
    parser_version: str | None
    mapping_version: str | None
    preflight_profile: str | None
    notes: str

    @classmethod
    def from_descriptor(cls, source: SourceDescriptor) -> "SourceProfile":
        return cls(
            source_id=source.source_id,
            role=source.role,
            authoritative=source.authoritative,
            active_now=source.active_now,
            pipeline_ready=source.pipeline_ready,
            adapter_kind=source.adapter_kind,
            transport=source.transport,
            data_format=source.data_format,
            update_semantics=source.update_semantics,
            pipeline_ids=source.pipeline_ids,
            parser_version=source.parser_version,
            mapping_version=source.mapping_version,
            preflight_profile=source.preflight_profile,
            notes=source.notes,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "role": self.role.value,
            "authoritative": self.authoritative,
            "active_now": self.active_now,
            "pipeline_ready": self.pipeline_ready,
            "adapter_kind": self.adapter_kind.value,
            "transport": self.transport.value,
            "data_format": self.data_format.value,
            "update_semantics": self.update_semantics.value,
            "pipeline_ids": list(self.pipeline_ids),
            "parser_version": self.parser_version,
            "mapping_version": self.mapping_version,
            "preflight_profile": self.preflight_profile,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class CountryProfile:
    """Factory-facing, immutable projection of a jurisdiction ``CountryPack``."""

    jurisdiction: str
    store_schema: str
    maturity: JurisdictionStage
    identity_fields: tuple[str, ...]
    observation_domains: tuple[ObservationDomain, ...]
    current_projection_mode: CurrentProjectionMode
    current_ordering_fields: tuple[str, ...]
    tombstone_supported: bool
    asset_mode: AssetMode
    native_tables: tuple[str, ...]
    aliases: tuple[str, ...]
    sources: tuple[SourceProfile, ...]

    @classmethod
    def from_pack(cls, pack: CountryPack) -> "CountryProfile":
        return cls(
            jurisdiction=pack.jurisdiction,
            store_schema=pack.store_schema,
            maturity=pack.maturity,
            identity_fields=pack.identity.fields,
            observation_domains=pack.observation_domains,
            current_projection_mode=pack.current_projection.mode,
            current_ordering_fields=pack.current_projection.ordering_fields,
            tombstone_supported=pack.current_projection.tombstone_supported,
            asset_mode=pack.asset_mode,
            native_tables=pack.native_tables,
            aliases=pack.aliases,
            sources=tuple(SourceProfile.from_descriptor(source) for source in pack.sources),
        )

    def source(self, source_id: str) -> SourceProfile:
        wanted = source_id.strip()
        for source in self.sources:
            if source.source_id == wanted:
                return source
        raise ValueError(f"unsupported source for {self.jurisdiction}: {source_id}")

    def as_dict(self) -> dict[str, object]:
        return {
            "jurisdiction": self.jurisdiction,
            "store_schema": self.store_schema,
            "maturity": self.maturity.value,
            "identity_fields": list(self.identity_fields),
            "observation_domains": [domain.value for domain in self.observation_domains],
            "current_projection": {
                "mode": self.current_projection_mode.value,
                "ordering_fields": list(self.current_ordering_fields),
                "tombstone_supported": self.tombstone_supported,
            },
            "asset_mode": self.asset_mode.value,
            "native_tables": list(self.native_tables),
            "aliases": list(self.aliases),
            "sources": [source.as_dict() for source in self.sources],
        }
