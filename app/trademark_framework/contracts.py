from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class SourceRole(StrEnum):
    PRIMARY = "PRIMARY"
    HISTORICAL_SEED = "HISTORICAL_SEED"
    INCREMENTAL = "INCREMENTAL"
    ENRICHMENT = "ENRICHMENT"
    REFERENCE = "REFERENCE"


class SourceAdapterKind(StrEnum):
    EXISTING_SUBSYSTEM = "EXISTING_SUBSYSTEM"
    DELIMITED_FILE = "DELIMITED_FILE"
    ZIP_XML = "ZIP_XML"
    XML_FILE = "XML_FILE"
    MULTI_TABLE_FILES = "MULTI_TABLE_FILES"
    REST_API = "REST_API"
    SOAP_API = "SOAP_API"
    SFTP_BULK = "SFTP_BULK"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    UNRESOLVED = "UNRESOLVED"


class TransportKind(StrEnum):
    EXISTING = "EXISTING"
    FILE = "FILE"
    HTTP_API = "HTTP_API"
    SOAP_API = "SOAP_API"
    SFTP = "SFTP"
    REFERENCE = "REFERENCE"
    UNRESOLVED = "UNRESOLVED"


class DataFormat(StrEnum):
    NATIVE = "NATIVE"
    TXT = "TXT"
    CSV = "CSV"
    TSV = "TSV"
    XML = "XML"
    ST96_XML = "ST96_XML"
    JSON = "JSON"
    MULTI_TABLE = "MULTI_TABLE"
    API = "API"
    UNKNOWN = "UNKNOWN"


class UpdateSemantics(StrEnum):
    EXISTING_DOMAIN = "EXISTING_DOMAIN"
    SNAPSHOT = "SNAPSHOT"
    HISTORICAL_SEED = "HISTORICAL_SEED"
    APPEND_ONLY = "APPEND_ONLY"
    UPSERT_CURRENT = "UPSERT_CURRENT"
    UPDATE_DELETE = "UPDATE_DELETE"
    API_CURRENT = "API_CURRENT"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    UNRESOLVED = "UNRESOLVED"


class CurrentProjectionMode(StrEnum):
    EXISTING_SUBSYSTEM = "EXISTING_SUBSYSTEM"
    SOURCE_NATIVE_CURRENT = "SOURCE_NATIVE_CURRENT"
    MANIFEST_ORDERED = "MANIFEST_ORDERED"
    HISTORICAL_ONLY = "HISTORICAL_ONLY"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class AssetMode(StrEnum):
    EXISTING_SUBSYSTEM = "EXISTING_SUBSYSTEM"
    NONE = "NONE"
    SOURCE_REFERENCE_ONLY = "SOURCE_REFERENCE_ONLY"
    OBJECT_STORE = "OBJECT_STORE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class ObservationDomain(StrEnum):
    RECORD = "RECORD"
    PARTY = "PARTY"
    GOODS_SERVICE = "GOODS_SERVICE"
    EVENT = "EVENT"
    RELATIONSHIP = "RELATIONSHIP"
    CLASSIFICATION = "CLASSIFICATION"
    DESCRIPTION = "DESCRIPTION"
    ASSET = "ASSET"
    SOURCE_OPERATION = "SOURCE_OPERATION"


class JurisdictionStage(StrEnum):
    SOURCE_FOUND = "SOURCE_FOUND"
    SOURCE_PROFILED = "SOURCE_PROFILED"
    PREFLIGHT_READY = "PREFLIGHT_READY"
    PARSER_READY = "PARSER_READY"
    COUNTRY_STORE_READY = "COUNTRY_STORE_READY"
    HISTORY_READY = "HISTORY_READY"
    CURRENT_PROJECTION_READY = "CURRENT_PROJECTION_READY"
    ASSET_READY = "ASSET_READY"
    PILOT_VALIDATED = "PILOT_VALIDATED"
    RELEASE_ACCEPTED = "RELEASE_ACCEPTED"
    PRODUCTION_CURRENT = "PRODUCTION_CURRENT"


@dataclass(frozen=True, slots=True)
class IdentityContract:
    fields: tuple[str, ...]
    source_declared: bool = True
    notes: str = ""

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not self.fields:
            errors.append("identity.fields must not be empty")
        if any(not field_name.strip() for field_name in self.fields):
            errors.append("identity.fields must not contain blanks")
        if len(set(self.fields)) != len(self.fields):
            errors.append("identity.fields must be unique")
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class CurrentProjectionContract:
    mode: CurrentProjectionMode
    ordering_fields: tuple[str, ...] = ()
    tombstone_supported: bool = False
    notes: str = ""

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.mode == CurrentProjectionMode.MANIFEST_ORDERED and not self.ordering_fields:
            errors.append("manifest-ordered current projection requires ordering_fields")
        if self.mode != CurrentProjectionMode.MANIFEST_ORDERED and self.ordering_fields:
            errors.append("ordering_fields are only valid for MANIFEST_ORDERED projection")
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class PipelineRoute:
    pipeline_id: str
    metadata_key: str | None = None
    metadata_value: str | None = None

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not self.pipeline_id.strip():
            errors.append("pipeline route requires pipeline_id")
        if (self.metadata_key is None) != (self.metadata_value is None):
            errors.append("pipeline route metadata_key/metadata_value must be set together")
        if self.metadata_key is not None and not self.metadata_key.strip():
            errors.append("pipeline route metadata_key must not be blank")
        if self.metadata_value is not None and not self.metadata_value.strip():
            errors.append("pipeline route metadata_value must not be blank")
        return tuple(errors)

    def matches(self, metadata: Mapping[str, object]) -> bool:
        if self.metadata_key is None:
            return True
        actual = str(metadata.get(self.metadata_key) or "").strip()
        return actual == self.metadata_value


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    source_id: str
    role: SourceRole
    authoritative: bool
    active_now: bool
    pipeline_ready: bool
    adapter_kind: SourceAdapterKind
    transport: TransportKind
    data_format: DataFormat
    update_semantics: UpdateSemantics
    pipeline_ids: tuple[str, ...] = ()
    pipeline_routes: tuple[PipelineRoute, ...] = ()
    parser_version: str | None = None
    mapping_version: str | None = None
    preflight_profile: str | None = None
    notes: str = ""

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not self.source_id.strip():
            errors.append("source_id is required")
        if self.pipeline_ready and not self.pipeline_ids:
            errors.append(f"{self.source_id}: pipeline_ready requires pipeline_ids")
        if len(set(self.pipeline_ids)) != len(self.pipeline_ids):
            errors.append(f"{self.source_id}: pipeline_ids must be unique")
        if any(not pipeline_id.strip() for pipeline_id in self.pipeline_ids):
            errors.append(f"{self.source_id}: pipeline_ids must not contain blanks")
        route_keys: set[tuple[str | None, str | None]] = set()
        routed_ids: set[str] = set()
        for route in self.pipeline_routes:
            errors.extend(f"{self.source_id}: {error}" for error in route.validate())
            key = (route.metadata_key, route.metadata_value)
            if key in route_keys:
                errors.append(f"{self.source_id}: duplicate pipeline route {key!r}")
            route_keys.add(key)
            routed_ids.add(route.pipeline_id)
            if route.pipeline_id not in self.pipeline_ids:
                errors.append(
                    f"{self.source_id}: route pipeline {route.pipeline_id} missing from pipeline_ids"
                )
        if len(self.pipeline_ids) > 1 and routed_ids != set(self.pipeline_ids):
            errors.append(
                f"{self.source_id}: multi-pipeline source requires routes covering every pipeline_id"
            )
        if self.pipeline_ready and self.adapter_kind in {
            SourceAdapterKind.REFERENCE_ONLY,
            SourceAdapterKind.UNRESOLVED,
        }:
            errors.append(
                f"{self.source_id}: {self.adapter_kind.value} source cannot be pipeline_ready"
            )
        if self.role == SourceRole.REFERENCE and self.update_semantics != UpdateSemantics.REFERENCE_ONLY:
            errors.append(f"{self.source_id}: REFERENCE role requires REFERENCE_ONLY semantics")
        if self.adapter_kind == SourceAdapterKind.REST_API and self.transport != TransportKind.HTTP_API:
            errors.append(f"{self.source_id}: REST_API requires HTTP_API transport")
        if self.adapter_kind == SourceAdapterKind.SOAP_API and self.transport != TransportKind.SOAP_API:
            errors.append(f"{self.source_id}: SOAP_API requires SOAP_API transport")
        if self.adapter_kind == SourceAdapterKind.SFTP_BULK and self.transport != TransportKind.SFTP:
            errors.append(f"{self.source_id}: SFTP_BULK requires SFTP transport")
        if self.adapter_kind == SourceAdapterKind.UNRESOLVED:
            if self.transport != TransportKind.UNRESOLVED:
                errors.append(f"{self.source_id}: UNRESOLVED adapter requires UNRESOLVED transport")
            if self.data_format != DataFormat.UNKNOWN:
                errors.append(f"{self.source_id}: UNRESOLVED adapter requires UNKNOWN data format")
            if self.update_semantics != UpdateSemantics.UNRESOLVED:
                errors.append(f"{self.source_id}: UNRESOLVED adapter requires UNRESOLVED semantics")
        return tuple(errors)

    def resolve_pipeline_id(self, metadata: Mapping[str, object]) -> str | None:
        if not self.pipeline_ids:
            return None
        if self.pipeline_routes:
            matched = [route.pipeline_id for route in self.pipeline_routes if route.matches(metadata)]
            if len(matched) > 1:
                raise RuntimeError(
                    f"ambiguous pipeline routing for {self.source_id}: matched {matched!r}"
                )
            return matched[0] if matched else None
        if len(self.pipeline_ids) == 1:
            return self.pipeline_ids[0]
        return None


@dataclass(frozen=True, slots=True)
class CountryPack:
    jurisdiction: str
    store_schema: str
    identity: IdentityContract
    observation_domains: tuple[ObservationDomain, ...]
    current_projection: CurrentProjectionContract
    asset_mode: AssetMode
    sources: tuple[SourceDescriptor, ...]
    maturity: JurisdictionStage = JurisdictionStage.SOURCE_FOUND
    native_tables: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    notes: str = ""
    extension_metadata: dict[str, object] = field(default_factory=dict)

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        jurisdiction = self.jurisdiction.strip().upper()
        if not jurisdiction:
            errors.append("jurisdiction is required")
        if not self.store_schema.strip():
            errors.append(f"{jurisdiction}: store_schema is required")
        errors.extend(f"{jurisdiction}: {error}" for error in self.identity.validate())
        errors.extend(
            f"{jurisdiction}: {error}" for error in self.current_projection.validate()
        )
        if ObservationDomain.RECORD not in self.observation_domains:
            errors.append(f"{jurisdiction}: RECORD observation domain is required")
        if len(set(self.observation_domains)) != len(self.observation_domains):
            errors.append(f"{jurisdiction}: observation_domains must be unique")
        if not self.sources:
            errors.append(f"{jurisdiction}: at least one source is required")
        source_ids = [source.source_id for source in self.sources]
        if len(set(source_ids)) != len(source_ids):
            errors.append(f"{jurisdiction}: source_id values must be unique")
        for source in self.sources:
            errors.extend(f"{jurisdiction}: {error}" for error in source.validate())
        aliases = [alias.strip().upper() for alias in self.aliases]
        if jurisdiction in aliases:
            errors.append(f"{jurisdiction}: aliases must not repeat jurisdiction")
        if len(set(aliases)) != len(aliases):
            errors.append(f"{jurisdiction}: aliases must be unique")
        return tuple(errors)

    @property
    def ready_sources(self) -> tuple[SourceDescriptor, ...]:
        return tuple(source for source in self.sources if source.pipeline_ready)

    def source(self, source_id: str) -> SourceDescriptor:
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
            "identity_fields": list(self.identity.fields),
            "observation_domains": [domain.value for domain in self.observation_domains],
            "current_projection": {
                "mode": self.current_projection.mode.value,
                "ordering_fields": list(self.current_projection.ordering_fields),
                "tombstone_supported": self.current_projection.tombstone_supported,
            },
            "asset_mode": self.asset_mode.value,
            "native_tables": list(self.native_tables),
            "aliases": list(self.aliases),
            "sources": [
                {
                    "source_id": source.source_id,
                    "role": source.role.value,
                    "authoritative": source.authoritative,
                    "active_now": source.active_now,
                    "pipeline_ready": source.pipeline_ready,
                    "adapter_kind": source.adapter_kind.value,
                    "transport": source.transport.value,
                    "data_format": source.data_format.value,
                    "update_semantics": source.update_semantics.value,
                    "pipeline_ids": list(source.pipeline_ids),
                    "pipeline_routes": [
                        {
                            "pipeline_id": route.pipeline_id,
                            "metadata_key": route.metadata_key,
                            "metadata_value": route.metadata_value,
                        }
                        for route in source.pipeline_routes
                    ],
                    "parser_version": source.parser_version,
                    "mapping_version": source.mapping_version,
                    "preflight_profile": source.preflight_profile,
                    "notes": source.notes,
                }
                for source in self.sources
            ],
            "notes": self.notes,
            "extension_metadata": dict(self.extension_metadata),
        }
