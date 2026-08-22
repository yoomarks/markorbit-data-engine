from __future__ import annotations

from dataclasses import dataclass

from app.trademark_factory import COUNTRY_FACTORY_VERSION
from app.trademark_factory.capabilities import (
    CAPABILITY_MATRIX_VERSION,
    CountryCapabilityReport,
    capability_matrix,
)
from app.trademark_factory.readiness import ReadinessReport, readiness_report
from app.trademark_factory.registry import FactoryRegistry, factory_registry
from app.trademark_framework.registry import FRAMEWORK_VERSION, framework_audit
from app.trademark_framework.scaffold import SCAFFOLD_VERSION


@dataclass(frozen=True, slots=True)
class CountryFactoryAudit:
    factory_version: str
    framework_version: str
    scaffold_version: str
    capability_matrix_version: str
    country_count: int
    source_count: int
    framework_ready: bool
    registry_ready: bool
    readiness: tuple[ReadinessReport, ...]
    capabilities: tuple[CountryCapabilityReport, ...]
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors and self.framework_ready and self.registry_ready

    def as_dict(self) -> dict[str, object]:
        return {
            "factory_version": self.factory_version,
            "framework_version": self.framework_version,
            "scaffold_version": self.scaffold_version,
            "capability_matrix_version": self.capability_matrix_version,
            "country_count": self.country_count,
            "source_count": self.source_count,
            "framework_ready": self.framework_ready,
            "registry_ready": self.registry_ready,
            "ready": self.ready,
            "readiness": [report.as_dict() for report in self.readiness],
            "capabilities": [report.as_dict() for report in self.capabilities],
            "errors": list(self.errors),
            "legal_conclusion": False,
        }


def audit_country_factory(registry: FactoryRegistry | None = None) -> CountryFactoryAudit:
    registry = registry or factory_registry()
    registry_audit = registry.audit()
    framework = framework_audit()
    readiness = tuple(readiness_report(pack) for pack in registry.packs)
    capabilities = capability_matrix(registry.packs)

    errors: list[str] = []
    errors.extend(framework.errors)
    errors.extend(registry_audit.errors)
    for report in readiness:
        errors.extend(
            f"{report.jurisdiction}: {warning}" for warning in report.structural_warnings
        )

    return CountryFactoryAudit(
        factory_version=COUNTRY_FACTORY_VERSION,
        framework_version=FRAMEWORK_VERSION,
        scaffold_version=SCAFFOLD_VERSION,
        capability_matrix_version=CAPABILITY_MATRIX_VERSION,
        country_count=registry_audit.country_count,
        source_count=registry_audit.source_count,
        framework_ready=framework.ready,
        registry_ready=registry_audit.ready,
        readiness=readiness,
        capabilities=capabilities,
        errors=tuple(errors),
    )
