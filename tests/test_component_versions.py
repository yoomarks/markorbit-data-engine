from pathlib import Path

from app.cn import CN_MODEL_VERSION
from app.cn.final_checkpoint import CHECKPOINT_VERSION as CN_CHECKPOINT_VERSION
from app.component_versions import (
    COMPONENT_MATRIX_VERSION,
    REPLAY_TELEMETRY_VERSION,
    STORAGE_POLICY_VERSION,
    component_versions,
)
from app.domain_lifecycle import LIFECYCLE_VERSION
from app.four_domain_acceptance import AUDIT_VERSION as FOUR_DOMAIN_ACCEPTANCE_VERSION
from app.global_trademarks.acceptance import GLOBAL_TRADEMARK_ACCEPTANCE_VERSION
from app.global_trademarks.migrations import GLOBAL_TRADEMARK_SCHEMA_VERSION
from app.global_trademarks.operator import GLOBAL_TRADEMARK_OPERATOR_VERSION
from app.integration_contract import CONTRACT_VERSION
from app.storage_headroom import HEADROOM_VERSION
from app.trademark_factory import COUNTRY_FACTORY_VERSION
from app.trademark_factory.capabilities import CAPABILITY_MATRIX_VERSION
from app.trademark_factory.mapping import MAPPING_CONTRACT_VERSION
from app.trademark_factory.native_ingest import NATIVE_INGEST_EXECUTOR_VERSION
from app.trademark_factory.readiness import READINESS_AUDIT_VERSION
from app.trademark_factory.registry import FACTORY_REGISTRY_VERSION
from app.trademark_factory.scaffold import FACTORY_SCAFFOLD_VERSION
from app.trademark_factory.store_bundle import NATIVE_STORE_BUNDLE_VERSION
from app.trademark_factory.writer import MAPPED_OBSERVATION_WRITER_VERSION
from app.trademark_framework.acquisition import ACQUISITION_FRAMEWORK_VERSION
from app.trademark_framework.http_acquisition import HTTP_ACQUISITION_ADAPTER_VERSION
from app.trademark_framework.http_transport import HTTP_TRANSPORT_VERSION
from app.trademark_framework.native_store import NATIVE_STORE_PRIMITIVES_VERSION
from app.trademark_framework.pagination import PAGINATION_HELPER_VERSION
from app.trademark_framework.registry import FRAMEWORK_VERSION
from app.trademark_framework.runtime import RUNTIME_ADAPTER_VERSION
from app.trademark_framework.scaffold import SCAFFOLD_VERSION
from app.us.alert_engine import ALERT_ENGINE_VERSION
from app.us.migrations import US_SCHEMA_VERSION
from app.us_assignment import ASSIGNMENT_SCHEMA_VERSION
from app.us_ttab import TTAB_SCHEMA_VERSION
from app.version import engine_version


ROOT = Path(__file__).resolve().parents[1]


def test_component_matrix_is_derived_from_owner_constants() -> None:
    report = component_versions()
    components = report["components"]

    assert report["matrix_version"] == COMPONENT_MATRIX_VERSION
    assert report["engine_release"] == engine_version()
    assert components["cn"]["model_version"] == CN_MODEL_VERSION
    assert components["cn"]["final_checkpoint_version"] == CN_CHECKPOINT_VERSION
    assert components["us_application"]["schema_version"] == US_SCHEMA_VERSION
    assert components["us_assignment"]["schema_version"] == ASSIGNMENT_SCHEMA_VERSION
    assert components["us_ttab"]["schema_version"] == TTAB_SCHEMA_VERSION
    assert components["us_alert_engine"]["version"] == ALERT_ENGINE_VERSION

    framework = components["trademark_jurisdiction_framework"]
    assert framework["version"] == FRAMEWORK_VERSION
    assert framework["runtime_adapter_version"] == RUNTIME_ADAPTER_VERSION
    assert framework["source_acquisition_version"] == ACQUISITION_FRAMEWORK_VERSION
    assert framework["http_transport_version"] == HTTP_TRANSPORT_VERSION
    assert framework["api_pagination_version"] == PAGINATION_HELPER_VERSION
    assert framework["http_acquisition_adapter_version"] == HTTP_ACQUISITION_ADAPTER_VERSION
    assert framework["country_scaffold_version"] == SCAFFOLD_VERSION
    assert framework["native_store_primitives_version"] == NATIVE_STORE_PRIMITIVES_VERSION

    factory = components["trademark_country_factory"]
    assert factory["version"] == COUNTRY_FACTORY_VERSION
    assert factory["registry_version"] == FACTORY_REGISTRY_VERSION
    assert factory["capability_matrix_version"] == CAPABILITY_MATRIX_VERSION
    assert factory["mapping_contract_version"] == MAPPING_CONTRACT_VERSION
    assert factory["mapped_observation_writer_version"] == MAPPED_OBSERVATION_WRITER_VERSION
    assert factory["native_store_bundle_version"] == NATIVE_STORE_BUNDLE_VERSION
    assert factory["native_ingest_executor_version"] == NATIVE_INGEST_EXECUTOR_VERSION
    assert factory["readiness_audit_version"] == READINESS_AUDIT_VERSION
    assert factory["scaffold_facade_version"] == FACTORY_SCAFFOLD_VERSION

    assert components["global_trademark"]["schema_version"] == GLOBAL_TRADEMARK_SCHEMA_VERSION
    assert components["global_trademark"]["operator_version"] == GLOBAL_TRADEMARK_OPERATOR_VERSION
    assert (
        components["global_trademark"]["acceptance_version"]
        == GLOBAL_TRADEMARK_ACCEPTANCE_VERSION
    )
    assert components["storage"]["policy_version"] == STORAGE_POLICY_VERSION
    assert components["storage"]["headroom_version"] == HEADROOM_VERSION
    assert components["storage"]["replay_telemetry_version"] == REPLAY_TELEMETRY_VERSION
    assert components["integration"]["contract_version"] == CONTRACT_VERSION
    assert components["domain_lifecycle"]["version"] == LIFECYCLE_VERSION
    assert components["four_domain_acceptance"]["version"] == FOUR_DOMAIN_ACCEPTANCE_VERSION


def test_readme_and_component_version_doc_track_current_versions() -> None:
    documentation = "\n".join(
        [
            (ROOT / "README.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "COMPONENT_VERSIONS.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "TRADEMARK_JURISDICTION_FRAMEWORK.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "TRADEMARK_SOURCE_ACQUISITION.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "TRADEMARK_HTTP_TRANSPORT.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "TRADEMARK_COUNTRY_FACTORY.md").read_text(encoding="utf-8"),
            (ROOT / "docs" / "TRADEMARK_NATIVE_STORE_PRIMITIVES.md").read_text(
                encoding="utf-8"
            ),
        ]
    )
    expected = {
        engine_version(),
        CN_MODEL_VERSION,
        US_SCHEMA_VERSION,
        ASSIGNMENT_SCHEMA_VERSION,
        TTAB_SCHEMA_VERSION,
        ALERT_ENGINE_VERSION,
        FRAMEWORK_VERSION,
        RUNTIME_ADAPTER_VERSION,
        ACQUISITION_FRAMEWORK_VERSION,
        HTTP_TRANSPORT_VERSION,
        PAGINATION_HELPER_VERSION,
        HTTP_ACQUISITION_ADAPTER_VERSION,
        SCAFFOLD_VERSION,
        NATIVE_STORE_PRIMITIVES_VERSION,
        COUNTRY_FACTORY_VERSION,
        FACTORY_REGISTRY_VERSION,
        CAPABILITY_MATRIX_VERSION,
        MAPPING_CONTRACT_VERSION,
        MAPPED_OBSERVATION_WRITER_VERSION,
        NATIVE_STORE_BUNDLE_VERSION,
        NATIVE_INGEST_EXECUTOR_VERSION,
        READINESS_AUDIT_VERSION,
        FACTORY_SCAFFOLD_VERSION,
        GLOBAL_TRADEMARK_SCHEMA_VERSION,
        GLOBAL_TRADEMARK_OPERATOR_VERSION,
        GLOBAL_TRADEMARK_ACCEPTANCE_VERSION,
        STORAGE_POLICY_VERSION,
        REPLAY_TELEMETRY_VERSION,
        CONTRACT_VERSION,
        LIFECYCLE_VERSION,
        FOUR_DOMAIN_ACCEPTANCE_VERSION,
    }
    for version in expected:
        assert version in documentation, version


def test_readme_no_longer_claims_us_m12_is_current() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "US M1.2 核心能力" not in readme
    assert "US M1.2 本地导入" not in readme
    assert "US Application M1.4" in readme
