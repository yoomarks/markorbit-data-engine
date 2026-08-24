from app.platform_contract import platform_contract
from app.work_engine_owners import (
    WORK_ENGINE_OWNER_REGISTRY_VERSION,
    work_engine_owner_registry,
)


def test_work_engine_owner_registry_has_two_distinct_real_owners() -> None:
    registry = work_engine_owner_registry()

    assert registry["version"] == WORK_ENGINE_OWNER_REGISTRY_VERSION
    assert registry["work_engine_version"] == "MARKORBIT_WORK_ENGINE_V1"
    assert registry["owner_count"] == 2
    assert registry["distinct_owner_scopes"] == [
        "CN_FINAL_PUBLISH",
        "CONTACT_COUNTRY_INFERENCE",
    ]
    assert registry["second_owner_scope"] == "CONTACT_COUNTRY_INFERENCE"
    assert registry["second_owner_is_non_cn"] is True
    assert registry["second_owner_runtime_fixture_proof"] is True
    assert registry["target_host_acceptance_claimed"] is False
    assert registry["release_promotion_authorized"] is False


def test_contact_country_owner_records_database_backed_resume_and_drift_proof() -> None:
    registry = work_engine_owner_registry()
    owners = {owner["owner_scope"]: owner for owner in registry["owners"]}

    contact = owners["CONTACT_COUNTRY_INFERENCE"]
    assert contact["checkpoint_version"] == "CONTACT_COUNTRY_INFERENCE_WORK_V1"
    assert contact["job_id_mapping"] == "CONTACT_COUNTRY_INFERENCE_RUN_ID"
    assert contact["persistence_adapter"] == "contact.country_inference_work_unit"
    assert contact["partition_kinds"] == ["ENTITY_RANGE"]
    assert contact["task_key_policy"] == "GENERIC_JOB_LOCAL_V1"

    proof = contact["runtime_proof"]
    assert proof["kind"] == "DATABASE_BACKED_SECOND_OWNER_FIXTURE"
    assert proof["database_backed"] is True
    assert proof["interruption_resume"] is True
    assert proof["committed_result_reconciliation"] is True
    assert proof["membership_drift_fail_closed"] is True
    assert proof["workflow"] == ".github/workflows/contact-country-inference-runtime.yml"
    assert proof["fixtures"] == [
        "app.contact_ingest.validate_country_inference_work_fixture",
        "app.contact_ingest.validate_country_inference_membership_guard_fixture",
    ]


def test_cn_owner_keeps_legacy_task_key_and_persistence_compatibility() -> None:
    registry = work_engine_owner_registry()
    owners = {owner["owner_scope"]: owner for owner in registry["owners"]}

    cn = owners["CN_FINAL_PUBLISH"]
    assert cn["checkpoint_version"] == "CN_FINAL_PUBLISH_V1"
    assert cn["job_id_mapping"] == "CN_PACKAGE_ID"
    assert cn["persistence_adapter"] == "control.cn_publish_subtask"
    assert cn["task_key_policy"] == "LEGACY_CN_V1_COMPATIBILITY"
    assert cn["runtime_proof"]["database_backed"] is True
    assert cn["runtime_proof"]["interruption_resume"] is True
    assert cn["runtime_proof"]["legacy_inflight_compatibility"] is True


def test_platform_contract_exposes_owner_registry() -> None:
    contract = platform_contract()
    registry = contract["work_engine_owners"]

    assert registry["version"] == WORK_ENGINE_OWNER_REGISTRY_VERSION
    assert registry["owner_count"] >= 2
    assert registry["second_owner_runtime_fixture_proof"] is True
