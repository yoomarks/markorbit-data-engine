"""Reusable trademark country-factory orchestration.

The factory consumes ``app.trademark_framework`` as its source of truth. It adds
capability/readiness/mapping/scaffold automation without replacing country-native stores,
source descriptors, runtime adapters or acceptance contracts.
"""

from app.trademark_factory.capabilities import (
    CAPABILITY_MATRIX_VERSION,
    CapabilityState,
    CountryCapabilityReport,
    SourceCapability,
    derive_country_capabilities,
)
from app.trademark_factory.mapping import (
    MAPPING_CONTRACT_VERSION,
    MappingContract,
    MappingRule,
    SelectorKind,
)
from app.trademark_factory.profile import CountryProfile, SourceProfile
from app.trademark_factory.readiness import READINESS_AUDIT_VERSION, ReadinessReport, readiness_report
from app.trademark_factory.registry import FACTORY_REGISTRY_VERSION, FactoryRegistry, factory_registry
from app.trademark_factory.scaffold import FACTORY_SCAFFOLD_VERSION, build_country_scaffold


COUNTRY_FACTORY_VERSION = "TRADEMARK_COUNTRY_FACTORY_V1"


__all__ = [
    "CAPABILITY_MATRIX_VERSION",
    "COUNTRY_FACTORY_VERSION",
    "FACTORY_REGISTRY_VERSION",
    "FACTORY_SCAFFOLD_VERSION",
    "MAPPING_CONTRACT_VERSION",
    "READINESS_AUDIT_VERSION",
    "CapabilityState",
    "CountryCapabilityReport",
    "CountryProfile",
    "FactoryRegistry",
    "MappingContract",
    "MappingRule",
    "ReadinessReport",
    "SelectorKind",
    "SourceCapability",
    "SourceProfile",
    "build_country_scaffold",
    "derive_country_capabilities",
    "factory_registry",
    "readiness_report",
]
