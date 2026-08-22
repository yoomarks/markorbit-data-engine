from __future__ import annotations

from dataclasses import dataclass

from app.trademark_framework.contracts import CountryPack, JurisdictionStage, SourceAdapterKind


READINESS_AUDIT_VERSION = "TRADEMARK_COUNTRY_FACTORY_READINESS_V1"

_STAGE_ORDER: tuple[JurisdictionStage, ...] = (
    JurisdictionStage.SOURCE_FOUND,
    JurisdictionStage.SOURCE_PROFILED,
    JurisdictionStage.PREFLIGHT_READY,
    JurisdictionStage.PARSER_READY,
    JurisdictionStage.COUNTRY_STORE_READY,
    JurisdictionStage.HISTORY_READY,
    JurisdictionStage.CURRENT_PROJECTION_READY,
    JurisdictionStage.ASSET_READY,
    JurisdictionStage.PILOT_VALIDATED,
    JurisdictionStage.RELEASE_ACCEPTED,
    JurisdictionStage.PRODUCTION_CURRENT,
)


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    jurisdiction: str
    declared_stage: JurisdictionStage
    reached: tuple[JurisdictionStage, ...]
    remaining: tuple[JurisdictionStage, ...]
    structural_warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "readiness_version": READINESS_AUDIT_VERSION,
            "jurisdiction": self.jurisdiction,
            "declared_stage": self.declared_stage.value,
            "reached": [stage.value for stage in self.reached],
            "remaining": [stage.value for stage in self.remaining],
            "structural_warnings": list(self.structural_warnings),
            "legal_conclusion": False,
        }


def stage_index(stage: JurisdictionStage) -> int:
    return _STAGE_ORDER.index(stage)


def stage_reached(current: JurisdictionStage, target: JurisdictionStage) -> bool:
    return stage_index(current) >= stage_index(target)


def readiness_report(pack: CountryPack) -> ReadinessReport:
    """Describe declared engineering maturity plus fail-closed structural warnings.

    The factory does not automatically promote maturity from the presence of files or
    tables. Promotion remains an explicit reviewed decision backed by source-specific
    fixtures/acceptance. This report only spots contradictions in the declared pack.
    """
    current_index = stage_index(pack.maturity)
    warnings: list[str] = []
    ready_sources = tuple(source for source in pack.sources if source.pipeline_ready)
    external_subsystem = bool(ready_sources) and all(
        source.adapter_kind == SourceAdapterKind.EXISTING_SUBSYSTEM for source in ready_sources
    )

    if stage_reached(pack.maturity, JurisdictionStage.SOURCE_PROFILED):
        if all(source.adapter_kind == SourceAdapterKind.UNRESOLVED for source in pack.sources):
            warnings.append("SOURCE_PROFILED declared but every source adapter is UNRESOLVED")
    if stage_reached(pack.maturity, JurisdictionStage.PREFLIGHT_READY):
        if ready_sources and not external_subsystem and not any(
            source.preflight_profile for source in ready_sources
        ):
            warnings.append("PREFLIGHT_READY-or-later declared but no ready source has preflight_profile")
    if stage_reached(pack.maturity, JurisdictionStage.PARSER_READY):
        if ready_sources and not external_subsystem and not any(
            source.parser_version for source in ready_sources
        ):
            warnings.append("PARSER_READY-or-later declared but no ready source has parser_version")
    if stage_reached(pack.maturity, JurisdictionStage.COUNTRY_STORE_READY) and not pack.native_tables:
        if not external_subsystem:
            warnings.append("COUNTRY_STORE_READY-or-later declared but native_tables is empty")
    if stage_reached(pack.maturity, JurisdictionStage.CURRENT_PROJECTION_READY):
        if pack.current_projection.mode.value in {"NOT_IMPLEMENTED", "HISTORICAL_ONLY"}:
            warnings.append(
                "CURRENT_PROJECTION_READY-or-later declared but current projection is not current-capable"
            )
    if stage_reached(pack.maturity, JurisdictionStage.PRODUCTION_CURRENT):
        if not any(source.authoritative and source.pipeline_ready for source in pack.sources):
            warnings.append(
                "PRODUCTION_CURRENT declared without an authoritative pipeline-ready source"
            )

    return ReadinessReport(
        jurisdiction=pack.jurisdiction,
        declared_stage=pack.maturity,
        reached=_STAGE_ORDER[: current_index + 1],
        remaining=_STAGE_ORDER[current_index + 1 :],
        structural_warnings=tuple(warnings),
    )
