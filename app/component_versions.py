from __future__ import annotations

from typing import Any

from app.cn import CN_MODEL_VERSION
from app.cn.final_checkpoint import CHECKPOINT_VERSION as CN_CHECKPOINT_VERSION
from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.task_migrations import CONTACT_TASK_CONTROL_VERSION
from app.domain_lifecycle import LIFECYCLE_VERSION
from app.four_domain_acceptance import AUDIT_VERSION as FOUR_DOMAIN_ACCEPTANCE_VERSION
from app.integration_contract import CONTRACT_VERSION
from app.storage_headroom import HEADROOM_VERSION
from app.us.alert_engine import ALERT_ENGINE_VERSION
from app.us.migrations import US_SCHEMA_VERSION
from app.us_assignment import ASSIGNMENT_SCHEMA_VERSION
from app.us_ttab import TTAB_SCHEMA_VERSION
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
            "us_application": {
                "schema_version": US_SCHEMA_VERSION,
            },
            "us_assignment": {
                "schema_version": ASSIGNMENT_SCHEMA_VERSION,
            },
            "us_ttab": {
                "schema_version": TTAB_SCHEMA_VERSION,
            },
            "us_alert_engine": {
                "version": ALERT_ENGINE_VERSION,
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
