from __future__ import annotations

from dataclasses import dataclass

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


FRAMEWORK_VERSION = "TRADEMARK_JURISDICTION_FRAMEWORK_V1"


_US = CountryPack(
    jurisdiction="US",
    store_schema="us",
    maturity=JurisdictionStage.PRODUCTION_CURRENT,
    identity=IdentityContract(
        fields=("serial_number",),
        notes="Existing USPTO subsystems remain authoritative; this pack describes them, not replaces them.",
    ),
    observation_domains=(
        ObservationDomain.RECORD,
        ObservationDomain.PARTY,
        ObservationDomain.GOODS_SERVICE,
        ObservationDomain.EVENT,
        ObservationDomain.RELATIONSHIP,
        ObservationDomain.ASSET,
    ),
    current_projection=CurrentProjectionContract(
        mode=CurrentProjectionMode.EXISTING_SUBSYSTEM,
        notes="US current/history semantics remain owned by the established USPTO domain implementations.",
    ),
    asset_mode=AssetMode.EXISTING_SUBSYSTEM,
    sources=(
        SourceDescriptor(
            source_id="USPTO_OFFICIAL",
            role=SourceRole.PRIMARY,
            authoritative=True,
            active_now=True,
            pipeline_ready=True,
            adapter_kind=SourceAdapterKind.EXISTING_SUBSYSTEM,
            transport=TransportKind.EXISTING,
            data_format=DataFormat.NATIVE,
            update_semantics=UpdateSemantics.EXISTING_DOMAIN,
            pipeline_ids=("USPTO_EXISTING_SUBSYSTEM",),
            notes="Application/TSDR/assignment/TTAB pipelines remain the source of truth.",
        ),
        SourceDescriptor(
            source_id="TM_LINK_US",
            role=SourceRole.REFERENCE,
            authoritative=False,
            active_now=False,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.REFERENCE_ONLY,
            transport=TransportKind.REFERENCE,
            data_format=DataFormat.CSV,
            update_semantics=UpdateSemantics.REFERENCE_ONLY,
            notes="Not ingested because official USPTO data is richer and newer.",
        ),
    ),
    notes="Compatibility pack for an already mature jurisdiction implementation.",
)


_GB = CountryPack(
    jurisdiction="GB",
    aliases=("UK",),
    store_schema="trademark_gb",
    maturity=JurisdictionStage.COUNTRY_STORE_READY,
    identity=IdentityContract(fields=("application_number",)),
    observation_domains=(
        ObservationDomain.RECORD,
        ObservationDomain.PARTY,
        ObservationDomain.CLASSIFICATION,
        ObservationDomain.EVENT,
        ObservationDomain.RELATIONSHIP,
    ),
    current_projection=CurrentProjectionContract(
        mode=CurrentProjectionMode.NOT_IMPLEMENTED,
        notes="2018 baseline is ingestible, but weekly/comparable-right current reconstruction is not complete.",
    ),
    asset_mode=AssetMode.NOT_IMPLEMENTED,
    sources=(
        SourceDescriptor(
            source_id="UKIPO_OPEN_DATA_2018",
            role=SourceRole.HISTORICAL_SEED,
            authoritative=True,
            active_now=True,
            pipeline_ready=True,
            adapter_kind=SourceAdapterKind.DELIMITED_FILE,
            transport=TransportKind.FILE,
            data_format=DataFormat.TXT,
            update_semantics=UpdateSemantics.HISTORICAL_SEED,
            pipeline_ids=("UKIPO_2018_DOMESTIC_V1", "UKIPO_2018_MADRID_IR_V1"),
            parser_version="UKIPO_2018_V1",
            mapping_version="COUNTRY_NATIVE_V1",
            preflight_profile="GB_2018_PIPE",
            notes="Domestic and Madrid-to-UK pipe-delimited historical baseline.",
        ),
        SourceDescriptor(
            source_id="UKIPO_WEEKLY",
            role=SourceRole.INCREMENTAL,
            authoritative=True,
            active_now=True,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.ZIP_XML,
            transport=TransportKind.FILE,
            data_format=DataFormat.XML,
            update_semantics=UpdateSemantics.APPEND_ONLY,
            notes="Official weekly source exists; loader/current projection is not implemented yet.",
        ),
        SourceDescriptor(
            source_id="UKIPO_COMPARABLE_RIGHTS",
            role=SourceRole.HISTORICAL_SEED,
            authoritative=True,
            active_now=True,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.DELIMITED_FILE,
            transport=TransportKind.FILE,
            data_format=DataFormat.UNKNOWN,
            update_semantics=UpdateSemantics.HISTORICAL_SEED,
            notes="Brexit comparable-right population; dedicated loader remains pending.",
        ),
        SourceDescriptor(
            source_id="UKIPO_DETAIL_PAGE",
            role=SourceRole.ENRICHMENT,
            authoritative=True,
            active_now=False,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.REST_API,
            transport=TransportKind.HTTP_API,
            data_format=DataFormat.API,
            update_semantics=UpdateSemantics.API_CURRENT,
            notes="Demand-driven enrichment only; do not bypass human-verification controls.",
        ),
        SourceDescriptor(
            source_id="TM_LINK_GB",
            role=SourceRole.REFERENCE,
            authoritative=False,
            active_now=False,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.REFERENCE_ONLY,
            transport=TransportKind.REFERENCE,
            data_format=DataFormat.CSV,
            update_semantics=UpdateSemantics.REFERENCE_ONLY,
            notes="Reference-only derivative of the thinner 2018 source.",
        ),
    ),
    native_tables=("historical_mark", "weekly_observation", "comparable_right"),
)


_EU = CountryPack(
    jurisdiction="EU",
    aliases=("EM",),
    store_schema="trademark_eu",
    maturity=JurisdictionStage.COUNTRY_STORE_READY,
    identity=IdentityContract(fields=("application_number",)),
    observation_domains=(
        ObservationDomain.RECORD,
        ObservationDomain.PARTY,
        ObservationDomain.CLASSIFICATION,
    ),
    current_projection=CurrentProjectionContract(
        mode=CurrentProjectionMode.HISTORICAL_ONLY,
        notes="TM-Link rows remain current_state_verified=false until an official EUIPO observation arrives.",
    ),
    asset_mode=AssetMode.NOT_IMPLEMENTED,
    sources=(
        SourceDescriptor(
            source_id="TM_LINK_EU",
            role=SourceRole.HISTORICAL_SEED,
            authoritative=False,
            active_now=True,
            pipeline_ready=True,
            adapter_kind=SourceAdapterKind.MULTI_TABLE_FILES,
            transport=TransportKind.FILE,
            data_format=DataFormat.CSV,
            update_semantics=UpdateSemantics.HISTORICAL_SEED,
            pipeline_ids=(
                "TM_LINK_EU_APPLICATIONS_V1",
                "TM_LINK_EU_APPLICANTS_V1",
                "TM_LINK_EU_TRADEMARK_DETAILS_V1",
                "TM_LINK_EU_NICE_CLASS_V1",
            ),
            parser_version="TM_LINK_SEED_V1",
            mapping_version="COUNTRY_NATIVE_V1",
            preflight_profile="TM_LINK",
            notes="Temporary historical seed only.",
        ),
        SourceDescriptor(
            source_id="EUIPO_API",
            role=SourceRole.ENRICHMENT,
            authoritative=True,
            active_now=False,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.REST_API,
            transport=TransportKind.HTTP_API,
            data_format=DataFormat.API,
            update_semantics=UpdateSemantics.API_CURRENT,
            notes="Future official refresh/enrichment source; access/rate limits must be respected.",
        ),
    ),
    native_tables=("seed_mark", "api_observation"),
)


_CA = CountryPack(
    jurisdiction="CA",
    store_schema="trademark_ca",
    maturity=JurisdictionStage.CURRENT_PROJECTION_READY,
    identity=IdentityContract(
        fields=("application_number", "extension_counter"),
        notes="CIPO ST.96 identity preserves the extension counter; application number alone is insufficient.",
    ),
    observation_domains=(
        ObservationDomain.RECORD,
        ObservationDomain.PARTY,
        ObservationDomain.GOODS_SERVICE,
        ObservationDomain.EVENT,
        ObservationDomain.RELATIONSHIP,
        ObservationDomain.SOURCE_OPERATION,
        ObservationDomain.ASSET,
    ),
    current_projection=CurrentProjectionContract(
        mode=CurrentProjectionMode.MANIFEST_ORDERED,
        ordering_fields=("source_period_end", "source_precedence", "source_sequence"),
        tombstone_supported=True,
        notes="Update/Delete history is append-only; current winner is ordered by source evidence, not ingestion time.",
    ),
    asset_mode=AssetMode.NOT_IMPLEMENTED,
    sources=(
        SourceDescriptor(
            source_id="CIPO_GLOBAL_2025_06_14",
            role=SourceRole.PRIMARY,
            authoritative=True,
            active_now=True,
            pipeline_ready=True,
            adapter_kind=SourceAdapterKind.ZIP_XML,
            transport=TransportKind.FILE,
            data_format=DataFormat.ST96_XML,
            update_semantics=UpdateSemantics.SNAPSHOT,
            pipeline_ids=("CIPO_ST96_CORE_V1",),
            parser_version="CIPO_ST96_CORE_V1",
            mapping_version="COUNTRY_NATIVE_V1",
            preflight_profile="CIPO_ST96",
            notes="Authoritative ST.96 GLOBAL baseline.",
        ),
        SourceDescriptor(
            source_id="CIPO_WEEKLY",
            role=SourceRole.INCREMENTAL,
            authoritative=True,
            active_now=True,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.ZIP_XML,
            transport=TransportKind.FILE,
            data_format=DataFormat.ST96_XML,
            update_semantics=UpdateSemantics.UPDATE_DELETE,
            pipeline_ids=("CIPO_ST96_CORE_V1",),
            parser_version="CIPO_ST96_CORE_V1",
            mapping_version="COUNTRY_NATIVE_V1",
            preflight_profile="CIPO_ST96",
            notes="Update/Delete parsing and ordered current projection exist; assets and real-package validation remain gates.",
        ),
        SourceDescriptor(
            source_id="TM_LINK_CA",
            role=SourceRole.REFERENCE,
            authoritative=False,
            active_now=False,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.REFERENCE_ONLY,
            transport=TransportKind.REFERENCE,
            data_format=DataFormat.CSV,
            update_semantics=UpdateSemantics.REFERENCE_ONLY,
            notes="Reference-only because CIPO ST.96 is richer and newer.",
        ),
    ),
    native_tables=(
        "st96_record",
        "party",
        "goods_service",
        "event",
        "relationship",
        "asset",
        "record_state",
        "record_operation",
        "current_source_order",
    ),
    extension_metadata={
        "rich_observation_version": "CIPO_ST96_RICH_OBSERVATION_V1",
        "current_projection_version": "CIPO_ST96_CURRENT_PROJECTION_V1",
    },
)


_AU = CountryPack(
    jurisdiction="AU",
    store_schema="trademark_au",
    maturity=JurisdictionStage.COUNTRY_STORE_READY,
    identity=IdentityContract(fields=("application_number", "ip_right_type")),
    observation_domains=(
        ObservationDomain.RECORD,
        ObservationDomain.PARTY,
        ObservationDomain.EVENT,
        ObservationDomain.RELATIONSHIP,
        ObservationDomain.CLASSIFICATION,
        ObservationDomain.DESCRIPTION,
    ),
    current_projection=CurrentProjectionContract(
        mode=CurrentProjectionMode.NOT_IMPLEMENTED,
        notes="Preserve IPGOD six-table source facts first; country-current projection remains separate.",
    ),
    asset_mode=AssetMode.NONE,
    sources=(
        SourceDescriptor(
            source_id="IPGOD_2022",
            role=SourceRole.PRIMARY,
            authoritative=True,
            active_now=True,
            pipeline_ready=True,
            adapter_kind=SourceAdapterKind.MULTI_TABLE_FILES,
            transport=TransportKind.FILE,
            data_format=DataFormat.MULTI_TABLE,
            update_semantics=UpdateSemantics.SNAPSHOT,
            pipeline_ids=(
                "IPGOD_2022_APPLICATION_V1",
                "IPGOD_2022_PARTY_ACTIVITY_V1",
                "IPGOD_2022_APPLICATION_LINKS_V1",
                "IPGOD_2022_APPLICATION_EVENTS_V1",
                "IPGOD_2022_APPLICATION_CLASSIFICATION_V1",
                "IPGOD_2022_APPLICATION_DESCRIPTION_V1",
            ),
            parser_version="IPGOD_2022_V1",
            mapping_version="COUNTRY_NATIVE_V1",
            preflight_profile="AU_IPGOD",
            notes="Preserve the six source domains rather than flattening them.",
        ),
        SourceDescriptor(
            source_id="AU_FUTURE_FRESHNESS",
            role=SourceRole.INCREMENTAL,
            authoritative=True,
            active_now=False,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.REST_API,
            transport=TransportKind.HTTP_API,
            data_format=DataFormat.API,
            update_semantics=UpdateSemantics.API_CURRENT,
            notes="Placeholder only for a later official post-IPGOD freshness source.",
        ),
        SourceDescriptor(
            source_id="TM_LINK_AU",
            role=SourceRole.REFERENCE,
            authoritative=False,
            active_now=False,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.REFERENCE_ONLY,
            transport=TransportKind.REFERENCE,
            data_format=DataFormat.CSV,
            update_semantics=UpdateSemantics.REFERENCE_ONLY,
            notes="Reference-only because it derives from older IPGOD data and drops fields.",
        ),
    ),
    native_tables=(
        "application",
        "party_activity",
        "application_link",
        "application_event",
        "application_classification",
        "application_description",
    ),
)


_NZ = CountryPack(
    jurisdiction="NZ",
    store_schema="trademark_nz",
    maturity=JurisdictionStage.COUNTRY_STORE_READY,
    identity=IdentityContract(fields=("application_number",)),
    observation_domains=(
        ObservationDomain.RECORD,
        ObservationDomain.PARTY,
        ObservationDomain.CLASSIFICATION,
    ),
    current_projection=CurrentProjectionContract(
        mode=CurrentProjectionMode.HISTORICAL_ONLY,
        notes="Historical TM-Link seed is not verified current state.",
    ),
    asset_mode=AssetMode.NOT_IMPLEMENTED,
    sources=(
        SourceDescriptor(
            source_id="TM_LINK_NZ",
            role=SourceRole.HISTORICAL_SEED,
            authoritative=False,
            active_now=True,
            pipeline_ready=True,
            adapter_kind=SourceAdapterKind.MULTI_TABLE_FILES,
            transport=TransportKind.FILE,
            data_format=DataFormat.CSV,
            update_semantics=UpdateSemantics.HISTORICAL_SEED,
            pipeline_ids=(
                "TM_LINK_NZ_APPLICATIONS_V1",
                "TM_LINK_NZ_APPLICANTS_V1",
                "TM_LINK_NZ_TRADEMARK_DETAILS_V1",
                "TM_LINK_NZ_NICE_CLASS_V1",
            ),
            parser_version="TM_LINK_SEED_V1",
            mapping_version="COUNTRY_NATIVE_V1",
            preflight_profile="TM_LINK",
            notes="Historical thin seed.",
        ),
        SourceDescriptor(
            source_id="IPONZ_API",
            role=SourceRole.ENRICHMENT,
            authoritative=True,
            active_now=False,
            pipeline_ready=False,
            adapter_kind=SourceAdapterKind.REST_API,
            transport=TransportKind.HTTP_API,
            data_format=DataFormat.API,
            update_semantics=UpdateSemantics.API_CURRENT,
            notes="Future official update discovery/detail enrichment once access is confirmed usable.",
        ),
    ),
    native_tables=("seed_mark", "api_observation"),
)


_PACKS: tuple[CountryPack, ...] = (_US, _GB, _EU, _CA, _AU, _NZ)


@dataclass(frozen=True, slots=True)
class FrameworkAudit:
    framework_version: str
    country_count: int
    source_count: int
    ready_source_count: int
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "framework_version": self.framework_version,
            "country_count": self.country_count,
            "source_count": self.source_count,
            "ready_source_count": self.ready_source_count,
            "ready": self.ready,
            "maturity": {pack.jurisdiction: pack.maturity.value for pack in _PACKS},
            "errors": list(self.errors),
        }


def country_packs() -> tuple[CountryPack, ...]:
    return _PACKS


def _alias_map() -> dict[str, CountryPack]:
    mapping: dict[str, CountryPack] = {}
    for pack in _PACKS:
        for key in (pack.jurisdiction, *pack.aliases):
            normalized = key.strip().upper()
            if normalized in mapping:
                raise RuntimeError(f"duplicate trademark jurisdiction/alias: {normalized}")
            mapping[normalized] = pack
    return mapping


def country_pack(jurisdiction: str) -> CountryPack:
    key = jurisdiction.strip().upper()
    try:
        return _alias_map()[key]
    except KeyError as exc:
        raise ValueError(f"unsupported trademark jurisdiction: {jurisdiction}") from exc


def framework_audit() -> FrameworkAudit:
    errors: list[str] = []
    aliases: dict[str, str] = {}
    pipeline_owner: dict[str, str] = {}
    source_count = 0
    ready_source_count = 0

    for pack in _PACKS:
        errors.extend(pack.validate())
        for key in (pack.jurisdiction, *pack.aliases):
            normalized = key.strip().upper()
            existing = aliases.get(normalized)
            if existing is not None and existing != pack.jurisdiction:
                errors.append(
                    f"jurisdiction alias collision: {normalized} -> {existing}/{pack.jurisdiction}"
                )
            aliases[normalized] = pack.jurisdiction
        for source in pack.sources:
            source_count += 1
            if source.pipeline_ready:
                ready_source_count += 1
            for pipeline_id in source.pipeline_ids:
                # The CIPO GLOBAL and WEEKLY sources intentionally share one parser/loader
                # pipeline. Other duplicate pipeline IDs are treated as drift.
                owner = f"{pack.jurisdiction}:{source.source_id}"
                existing = pipeline_owner.get(pipeline_id)
                if existing is not None and {
                    existing,
                    owner,
                } != {
                    "CA:CIPO_GLOBAL_2025_06_14",
                    "CA:CIPO_WEEKLY",
                }:
                    errors.append(
                        f"pipeline_id collision: {pipeline_id} -> {existing}/{owner}"
                    )
                pipeline_owner[pipeline_id] = owner

    return FrameworkAudit(
        framework_version=FRAMEWORK_VERSION,
        country_count=len(_PACKS),
        source_count=source_count,
        ready_source_count=ready_source_count,
        errors=tuple(errors),
    )
