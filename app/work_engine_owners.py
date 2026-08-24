from __future__ import annotations

from typing import Any

from app.cn.publish_subtasks import (
    CHECKPOINT_VERSION as CN_CHECKPOINT_VERSION,
    WORK_OWNER_SCOPE as CN_WORK_OWNER_SCOPE,
)
from app.contact_ingest.country_inference_work import (
    CHECKPOINT_VERSION as CONTACT_CHECKPOINT_VERSION,
    PARTITION_KIND as CONTACT_PARTITION_KIND,
    WORK_OWNER_SCOPE as CONTACT_WORK_OWNER_SCOPE,
)


WORK_ENGINE_OWNER_REGISTRY_VERSION = "MARKORBIT_WORK_ENGINE_OWNER_REGISTRY_V1"


def work_engine_owner_registry() -> dict[str, Any]:
    """Describe concrete owners that execute through MARKORBIT_WORK_ENGINE_V1.

    This is a static code/proof registry. It records which adapters and CI fixtures
    exist in the repository; it does not claim that a target-host corpus acceptance
    has run, and it does not authorize release promotion.
    """
    owners = [
        {
            "owner_scope": CN_WORK_OWNER_SCOPE,
            "checkpoint_version": CN_CHECKPOINT_VERSION,
            "job_id_mapping": "CN_PACKAGE_ID",
            "persistence_adapter": "control.cn_publish_subtask",
            "task_key_policy": "LEGACY_CN_V1_COMPATIBILITY",
            "runtime_proof": {
                "kind": "DATABASE_BACKED_COMPATIBILITY_FIXTURES",
                "database_backed": True,
                "interruption_resume": True,
                "legacy_inflight_compatibility": True,
            },
        },
        {
            "owner_scope": CONTACT_WORK_OWNER_SCOPE,
            "checkpoint_version": CONTACT_CHECKPOINT_VERSION,
            "job_id_mapping": "CONTACT_COUNTRY_INFERENCE_RUN_ID",
            "persistence_adapter": "contact.country_inference_work_unit",
            "partition_kinds": [CONTACT_PARTITION_KIND],
            "task_key_policy": "GENERIC_JOB_LOCAL_V1",
            "runtime_proof": {
                "kind": "DATABASE_BACKED_SECOND_OWNER_FIXTURE",
                "database_backed": True,
                "interruption_resume": True,
                "committed_result_reconciliation": True,
                "membership_drift_fail_closed": True,
                "workflow": ".github/workflows/contact-country-inference-runtime.yml",
                "fixtures": [
                    "app.contact_ingest.validate_country_inference_work_fixture",
                    "app.contact_ingest.validate_country_inference_membership_guard_fixture",
                ],
            },
        },
    ]
    distinct_owner_scopes = sorted({str(owner["owner_scope"]) for owner in owners})
    second_owner = next(
        owner for owner in owners if owner["owner_scope"] == CONTACT_WORK_OWNER_SCOPE
    )
    second_runtime = second_owner["runtime_proof"]
    second_owner_runtime_fixture_proof = bool(
        second_runtime.get("database_backed")
        and second_runtime.get("interruption_resume")
        and second_runtime.get("membership_drift_fail_closed")
        and second_runtime.get("workflow")
        and len(second_runtime.get("fixtures") or []) >= 2
    )
    return {
        "version": WORK_ENGINE_OWNER_REGISTRY_VERSION,
        "work_engine_version": "MARKORBIT_WORK_ENGINE_V1",
        "owner_count": len(distinct_owner_scopes),
        "distinct_owner_scopes": distinct_owner_scopes,
        "owners": owners,
        "second_owner_scope": CONTACT_WORK_OWNER_SCOPE,
        "second_owner_is_non_cn": CONTACT_WORK_OWNER_SCOPE != CN_WORK_OWNER_SCOPE,
        "second_owner_runtime_fixture_proof": second_owner_runtime_fixture_proof,
        "target_host_acceptance_claimed": False,
        "release_promotion_authorized": False,
    }
