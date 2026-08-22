from __future__ import annotations

from dataclasses import dataclass

from app.trademark_framework.contracts import SourceRole
from app.trademark_framework.registry import country_pack, country_packs


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Backward-compatible Global Trademark source-plan projection.

    Rich source semantics now live in ``app.trademark_framework``. Existing Global
    Trademark consumers continue to receive the narrow fields they already depend on.
    """

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


def _compat_plan(jurisdiction: str) -> CountrySourcePlan:
    pack = country_pack(jurisdiction)
    return CountrySourcePlan(
        jurisdiction=pack.jurisdiction,
        store_schema=pack.store_schema,
        sources=tuple(
            SourceSpec(
                source_id=source.source_id,
                role=source.role,
                authoritative=source.authoritative,
                active_now=source.active_now,
                pipeline_ready=source.pipeline_ready,
                notes=source.notes,
            )
            for source in pack.sources
        ),
    )


COUNTRY_SOURCES: dict[str, CountrySourcePlan] = {
    pack.jurisdiction: _compat_plan(pack.jurisdiction) for pack in country_packs()
}


def country_plan(jurisdiction: str) -> CountrySourcePlan:
    pack = country_pack(jurisdiction)
    return COUNTRY_SOURCES[pack.jurisdiction]
