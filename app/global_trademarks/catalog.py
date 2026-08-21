from dataclasses import dataclass
from enum import StrEnum


class SourceRole(StrEnum):
    PRIMARY = "PRIMARY"
    HISTORICAL_SEED = "HISTORICAL_SEED"
    INCREMENTAL = "INCREMENTAL"
    ENRICHMENT = "ENRICHMENT"
    REFERENCE = "REFERENCE"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_id: str
    role: SourceRole
    authoritative: bool
    active_now: bool
    pipeline_ready: bool
    notes: str


@dataclass(frozen=True, slots=True)
class CountrySourcePlan:
    jurisdiction: str
    store_schema: str
    sources: tuple[SourceSpec, ...]


COUNTRY_SOURCES: dict[str, CountrySourcePlan] = {
    "US": CountrySourcePlan(
        jurisdiction="US",
        store_schema="us",
        sources=(
            SourceSpec(
                source_id="USPTO_OFFICIAL",
                role=SourceRole.PRIMARY,
                authoritative=True,
                active_now=True,
                pipeline_ready=True,
                notes="Existing USPTO application/TSDR/assignment/TTAB pipelines remain authoritative.",
            ),
            SourceSpec(
                source_id="TM_LINK_US",
                role=SourceRole.REFERENCE,
                authoritative=False,
                active_now=False,
                pipeline_ready=False,
                notes="Not used for ingestion; official USPTO data is richer and newer.",
            ),
        ),
    ),
    "GB": CountrySourcePlan(
        jurisdiction="GB",
        store_schema="trademark_gb",
        sources=(
            SourceSpec(
                source_id="UKIPO_OPEN_DATA_2018",
                role=SourceRole.HISTORICAL_SEED,
                authoritative=True,
                active_now=True,
                pipeline_ready=True,
                notes="Domestic and Madrid-to-UK TXT exports form the historical thin baseline.",
            ),
            SourceSpec(
                source_id="UKIPO_WEEKLY",
                role=SourceRole.INCREMENTAL,
                authoritative=True,
                active_now=True,
                pipeline_ready=False,
                notes="Source is available, but the weekly event/current-state ingestion pipeline is not implemented yet.",
            ),
            SourceSpec(
                source_id="UKIPO_COMPARABLE_RIGHTS",
                role=SourceRole.HISTORICAL_SEED,
                authoritative=True,
                active_now=True,
                pipeline_ready=False,
                notes="Source population is known, but comparable-right ingestion is not implemented yet.",
            ),
            SourceSpec(
                source_id="UKIPO_DETAIL_PAGE",
                role=SourceRole.ENRICHMENT,
                authoritative=True,
                active_now=False,
                pipeline_ready=False,
                notes="Demand-driven only; may be blocked by human-verification controls.",
            ),
            SourceSpec(
                source_id="TM_LINK_GB",
                role=SourceRole.REFERENCE,
                authoritative=False,
                active_now=False,
                pipeline_ready=False,
                notes="Not ingested because it is derived from the 2018 UKIPO files and is thinner.",
            ),
        ),
    ),
    "EU": CountrySourcePlan(
        jurisdiction="EU",
        store_schema="trademark_eu",
        sources=(
            SourceSpec(
                source_id="TM_LINK_EU",
                role=SourceRole.HISTORICAL_SEED,
                authoritative=False,
                active_now=True,
                pipeline_ready=True,
                notes="Temporary historical seed while original EUIPO bulk data is unavailable.",
            ),
            SourceSpec(
                source_id="EUIPO_API",
                role=SourceRole.ENRICHMENT,
                authoritative=True,
                active_now=False,
                pipeline_ready=False,
                notes="Future official refresh/enrichment source; access and rate limits must be respected.",
            ),
        ),
    ),
    "CA": CountrySourcePlan(
        jurisdiction="CA",
        store_schema="trademark_ca",
        sources=(
            SourceSpec(
                source_id="CIPO_GLOBAL_2025_06_14",
                role=SourceRole.PRIMARY,
                authoritative=True,
                active_now=True,
                pipeline_ready=True,
                notes=(
                    "ST.96 global snapshot baseline; core record plus source-faithful party, "
                    "goods/services, office-event and registry-relationship observations are "
                    "durable and resumable."
                ),
            ),
            SourceSpec(
                source_id="CIPO_WEEKLY",
                role=SourceRole.INCREMENTAL,
                authoritative=True,
                active_now=True,
                pipeline_ready=False,
                notes=(
                    "Update/Delete observations, source-presence tombstones and rich child "
                    "observation snapshots are implemented. Ordered source-current projection "
                    "and assets remain before full production readiness."
                ),
            ),
            SourceSpec(
                source_id="TM_LINK_CA",
                role=SourceRole.REFERENCE,
                authoritative=False,
                active_now=False,
                pipeline_ready=False,
                notes="Not used for ingestion because CIPO ST.96 is richer and newer.",
            ),
        ),
    ),
    "AU": CountrySourcePlan(
        jurisdiction="AU",
        store_schema="trademark_au",
        sources=(
            SourceSpec(
                source_id="IPGOD_2022",
                role=SourceRole.PRIMARY,
                authoritative=True,
                active_now=True,
                pipeline_ready=True,
                notes="Preserve the six-table snowflake model, then project country current state.",
            ),
            SourceSpec(
                source_id="AU_FUTURE_FRESHNESS",
                role=SourceRole.INCREMENTAL,
                authoritative=True,
                active_now=False,
                pipeline_ready=False,
                notes="Reserved for a later official source supplying post-IPGOD freshness.",
            ),
            SourceSpec(
                source_id="TM_LINK_AU",
                role=SourceRole.REFERENCE,
                authoritative=False,
                active_now=False,
                pipeline_ready=False,
                notes="Not used because it derives from older IPGOD data and discards fields.",
            ),
        ),
    ),
    "NZ": CountrySourcePlan(
        jurisdiction="NZ",
        store_schema="trademark_nz",
        sources=(
            SourceSpec(
                source_id="TM_LINK_NZ",
                role=SourceRole.HISTORICAL_SEED,
                authoritative=False,
                active_now=True,
                pipeline_ready=True,
                notes="Historical thin seed for application number, applicant, class and basic dates/text.",
            ),
            SourceSpec(
                source_id="IPONZ_API",
                role=SourceRole.ENRICHMENT,
                authoritative=True,
                active_now=False,
                pipeline_ready=False,
                notes="Future official update discovery and case-detail enrichment once access works.",
            ),
        ),
    ),
}


def country_plan(jurisdiction: str) -> CountrySourcePlan:
    key = jurisdiction.strip().upper()
    if key == "EM":
        key = "EU"
    try:
        return COUNTRY_SOURCES[key]
    except KeyError as exc:
        raise ValueError(f"unsupported trademark jurisdiction: {jurisdiction}") from exc
