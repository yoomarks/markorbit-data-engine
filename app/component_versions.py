from __future__ import annotations

from typing import Any

from app.cn import CN_MODEL_VERSION
from app.cn.final_checkpoint import CHECKPOINT_VERSION as CN_CHECKPOINT_VERSION
from app.cn_mark_image import SOURCE_VERSION as CN_MARK_IMAGE_SOURCE_VERSION
from app.cn_qcc import POLICY_VERSION as CN_QCC_POLICY_VERSION
from app.cn_qcc import SOURCE_VERSION as CN_QCC_SOURCE_VERSION
from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.task_migrations import CONTACT_TASK_CONTROL_VERSION
from app.domain_lifecycle import LIFECYCLE_VERSION
from app.four_domain_acceptance import AUDIT_VERSION as FOUR_DOMAIN_ACCEPTANCE_VERSION
from app.global_trademarks.acceptance import GLOBAL_TRADEMARK_ACCEPTANCE_VERSION
from app.global_trademarks.ca_current import CIPO_ST96_CURRENT_PROJECTION_VERSION
from app.global_trademarks.ca_rich_schema import CIPO_ST96_RICH_OBSERVATION_VERSION
from app.global_trademarks.migrations import GLOBAL_TRADEMARK_SCHEMA_VERSION
from app.global_trademarks.operator import GLOBAL_TRADEMARK_OPERATOR_VERSION
from app.integration_contract import CONTRACT_VERSION
from app.storage_headroom import HEADROOM_VERSION
from app.trademark_framework.registry import FRAMEWORK_VERSION as TRADEMARK_JURISDICTION_FRAMEWORK_VERSION
from app.trademark_framework.runtime import RUNTIME_ADAPTER_VERSION as TRADEMARK_RUNTIME_ADAPTER_VERSION
from app.us.alert_engine import ALERT_ENGINE_VERSION
from app.us.migrations import US_SCHEMA_VERSION
from app.us_assignment import ASSIGNMENT_SCHEMA_VERSION
from app.us_mark_image import SOURCE_VERSION as US_MARK_IMAGE_SOURCE_VERSION
from app.us_ttab import TTAB_SCHEMA_VERSION
from app.us_tsdr.adapter import TSDR_SOURCE_VERSION
from app.us_tsdr.policy import POLICY_VERSION as TSDR_ACQUISITION_POLICY_VERSION
from app.version import engine_version


COMPONENT_MATRIX_VERSION = "MARKORBIT_DATA_ENGINE_COMPONENT_MATRIX_V1"
STORAGE_POLICY_VERSION = "DATA_ENGINE_STORAGE_V2"
REPLAY_TELEMETRY_VERSION = "DATA_ENGINE_REPLAY_TELEMETRY_V1"


def component_versions() -> dict[str, Any]:
    """Return the authoritative machine-readable component version matrix.

    The root VERSION remains the Data Engine release marker. Domain/component
    versions are intentionally independent and must not be inferred from it.
    """

    return {
        "matrix_version": COMPONENT_MATRIX_VERSION,
        "engine_release": engine_version(),
        "components": {
            "cn": {
                "model_version": CN_MODEL_VERSION,
                "final_checkpoint_version": CN_CHECKPOINT_VERSION,
            },
            "cn_mark_image": {
                "source_version": CN_MARK_IMAGE_SOURCE_VERSION,
            },
            "cn_qcc": {
                "source_version": CN_QCC_SOURCE_VERSION,
                "acquisition_policy_version": CN_QCC_POLICY_VERSION,
            },
            "us_application": {
                "schema_version": US_SCHEMA_VERSION,
            },
            "us_assignment": {
                "schema_version": ASSIGNMENT_SCHEMA_VERSION,
            },
            "us_ttab": {
                "schema_version": TTAB_SCHEMA_VERSION,
            },
            "us_tsdr": {
                "source_version": TSDR_SOURCE_VERSION,
                "acquisition_policy_version": TSDR_ACQUISITION_POLICY_VERSION,
            },
            "us_mark_image": {
                "source_version": US_MARK_IMAGE_SOURCE_VERSION,
            },
            "us_alert_engine": {
                "version": ALERT_ENGINE_VERSION,
            },
            "trademark_jurisdiction_framework": {
                "version": TRADEMARK_JURISDICTION_FRAMEWORK_VERSION,
                "runtime_adapter_version": TRADEMARK_RUNTIME_ADAPTER_VERSION,
            },
            "global_trademark": {
                "schema_version": GLOBAL_TRADEMARK_SCHEMA_VERSION,
                "operator_version": GLOBAL_TRADEMARK_OPERATOR_VERSION,
                "acceptance_version": GLOBAL_TRADEMARK_ACCEPTANCE_VERSION,
                "cipo_st96_rich_observation_version": CIPO_ST96_RICH_OBSERVATION_VERSION,
                "cipo_st96_current_projection_version": CIPO_ST96_CURRENT_PROJECTION_VERSION,
            },
            "contact_ingestion": {
                "version": CONTACT_INGEST_VERSION,
                "task_control_version": CONTACT_TASK_CONTROL_VERSION,
            },
            "storage": {
                "policy_version": STORAGE_POLICY_VERSION,
                "headroom_version": HEADROOM_VERSION,
                "replay_telemetry_version": REPLAY_TELEMETRY_VERSION,
            },
            "integration": {
                "contract_version": CONTRACT_VERSION,
            },
            "domain_lifecycle": {
                "version": LIFECYCLE_VERSION,
            },
            "four_domain_acceptance": {
                "version": FOUR_DOMAIN_ACCEPTANCE_VERSION,
            },
        },
    }
