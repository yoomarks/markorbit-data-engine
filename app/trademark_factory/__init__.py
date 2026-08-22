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
from app.trademark_factory.native_ingest import (
    NATIVE_INGEST_RUNNER_VERSION,
    NativeIngestResult,
    NativeRecordEnvelope,
    native_ingest_contract_hash,
    run_native_ingest,
)
from app.trademark_factory.profile import CountryProfile, SourceProfile
from app.trademark_factory.readiness import READINESS_AUDIT_VERSION, ReadinessReport, readiness_report
from app.trademark_factory.registry import FACTORY_REGISTRY_VERSION, FactoryRegistry, factory_registry
from app.trademark_factory.scaffold import FACTORY_SCAFFOLD_VERSION, build_country_scaffold
from app.trademark_factory.store_bundle import (
    NATIVE_STORE_BUNDLE_VERSION,
    BundleAppendResult,
    NativeStoreBundle,
    StoreBinding,
    append_native_record_bundle,
    install_native_store_bundle,
)
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
    "NATIVE_INGEST_RUNNER_VERSION",
    "NATIVE_STORE_BUNDLE_VERSION",
    "READINESS_AUDIT_VERSION",
    "BundleAppendResult",
    "CapabilityState",
    "CountryCapabilityReport",
    "CountryProfile",
    "FactoryRegistry",
    "MappingContract",
    "MappingRule",
    "NativeIngestResult",
    "NativeRecordEnvelope",
    "NativeStoreBundle",
    "ReadinessReport",
    "SelectorKind",
    "SourceCapability",
    "SourceCapabilityReport",
    "SourceProfile",
    "StoreBinding",
    "append_mapped_observation",
    "append_native_record_bundle",
    "build_country_scaffold",
    "build_observation_row",
    "derive_country_capabilities",
    "derive_source_capabilities",
    "extract_domain_values",
    "factory_registry",
    "install_native_store_bundle",
    "native_ingest_contract_hash",
    "readiness_report",
    "run_native_ingest",
]
