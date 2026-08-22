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
    SourceCapabilityReport,
    derive_country_capabilities,
    derive_source_capabilities,
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
from app.trademark_factory.writer import (
    MAPPED_OBSERVATION_WRITER_VERSION,
    append_mapped_observation,
    build_observation_row,
    extract_domain_values,
)


COUNTRY_FACTORY_VERSION = "TRADEMARK_COUNTRY_FACTORY_V1"


__all__ = [
    "CAPABILITY_MATRIX_VERSION",
    "COUNTRY_FACTORY_VERSION",
    "FACTORY_REGISTRY_VERSION",
    "FACTORY_SCAFFOLD_VERSION",
    "MAPPED_OBSERVATION_WRITER_VERSION",
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
    "SourceCapabilityReport",
    "SourceProfile",
    "append_mapped_observation",
    "build_country_scaffold",
    "build_observation_row",
    "derive_country_capabilities",
    "derive_source_capabilities",
    "extract_domain_values",
    "factory_registry",
    "readiness_report",
]
