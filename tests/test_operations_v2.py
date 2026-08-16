from app import admin_system_api
from app.operations_v2 import (
    DomainTaskEvidence,
    PackageOperationEvidence,
    classify_domain_task_operation,
    classify_package_operation,
    operations_contract,
)


def test_final_publish_checkpoint_is_resume_candidate_and_preserves_work() -> None:
    operation = classify_package_operation(
        PackageOperationEvidence(
            package_id="pkg-1",
            domain="CN",
            package_status="FAILED",
            publish_checkpoint_version="CN_FINAL_PUBLISH_V1",
            publish_success_tasks=36,
            publish_failed_tasks=1,
            publish_task_total=120,
            publish_resume_task_index=37,
        )
    )

    assert operation["state"] == "RESUME_CANDIDATE"
    assert operation["next_safe_action"] == "VERIFY_FINAL_CHECKPOINT_THEN_CONTINUE_WORK_UNIT"
    assert operation["preserve_partial_state"] is True
    assert operation["verification_required"] is True
    assert operation["operator_required"] is False
    assert "COMPLETED_WORK_UNITS_MUST_NOT_BE_REPLAYED" in operation["reason_codes"]
    assert operation["progress"]["work_success"] == 36
    assert operation["progress"]["resume_task_index"] == 37
    assert operation["progress"]["progress_pct"] == 30.0


def test_stage_checkpoint_resumes_post_stage_instead_of_raw_replay() -> None:
    operation = classify_package_operation(
        PackageOperationEvidence(
            package_id="pkg-2",
            domain="CN",
            package_status="FAILED",
            stage_checkpoint_version="CN_M16_STAGE_V1",
        )
    )

    assert operation["state"] == "RESUME_CANDIDATE"
    assert operation["next_safe_action"] == "VERIFY_STAGE_CHECKPOINT_THEN_RESUME_POST_STAGE"
    assert operation["preserve_partial_state"] is True
    assert operation["verification_required"] is True


def test_failed_package_without_checkpoint_is_retry_candidate_not_resume() -> None:
    operation = classify_package_operation(
        PackageOperationEvidence(
            package_id="pkg-3",
            domain="US_APPLICATION",
            package_status="FAILED",
        )
    )

    assert operation["state"] == "RETRY_CANDIDATE"
    assert operation["next_safe_action"] == "VERIFY_SOURCE_AND_DOMAIN_GATES_THEN_RETRY_PACKAGE"
    assert operation["preserve_partial_state"] is False
    assert "NO_DURABLE_RESUME_CHECKPOINT" in operation["reason_codes"]


def test_missing_registered_source_blocks_retry() -> None:
    operation = classify_package_operation(
        PackageOperationEvidence(
            package_id="pkg-4",
            domain="CN",
            package_status="MISSING_FILE",
        )
    )

    assert operation["state"] == "BLOCKED"
    assert operation["next_safe_action"] == "RESTORE_OR_LOCATE_REGISTERED_SOURCE"
    assert operation["operator_required"] is True


def test_orphan_processing_state_fails_closed_to_operator_review() -> None:
    operation = classify_package_operation(
        PackageOperationEvidence(
            package_id="pkg-5",
            domain="CN",
            package_status="PROCESSING",
        )
    )

    assert operation["state"] == "NEEDS_OPERATOR"
    assert operation["operator_required"] is True
    assert operation["preserve_partial_state"] is True


def test_active_job_takes_precedence_over_checkpoint_candidate() -> None:
    operation = classify_package_operation(
        PackageOperationEvidence(
            package_id="pkg-6",
            domain="CN",
            package_status="PROCESSING",
            latest_job_status="RUNNING",
            stage_checkpoint_version="CN_M16_STAGE_V1",
        )
    )

    assert operation["state"] == "RUNNING"
    assert operation["next_safe_action"] == "WAIT_FOR_CURRENT_UNIT_BOUNDARY"


def test_cooperative_domain_stop_becomes_stopping_then_paused() -> None:
    running = classify_domain_task_operation(
        DomainTaskEvidence(
            run_id="run-1",
            domain="CN",
            action="CONTINUE",
            status="RUNNING",
            stop_requested=True,
        )
    )
    paused = classify_domain_task_operation(
        DomainTaskEvidence(
            run_id="run-1",
            domain="CN",
            action="CONTINUE",
            status="INTERRUPTED",
            stop_requested=True,
        )
    )

    assert running["state"] == "STOPPING"
    assert running["next_safe_action"] == "WAIT_FOR_SAFE_PACKAGE_BOUNDARY"
    assert paused["state"] == "PAUSED"
    assert paused["next_safe_action"] == "CONTINUE_THROUGH_EXISTING_DOMAIN_GATE"


def test_operations_contract_keeps_existing_gates_authoritative() -> None:
    contract = operations_contract()

    assert contract["version"] == "MARKORBIT_OPERATIONS_V2"
    assert contract["invariants"]["checkpoint_presence_is_candidate_not_proof"] is True
    assert contract["invariants"]["checkpoint_validation_required_before_resume"] is True
    assert contract["invariants"]["completed_work_units_must_not_be_replayed"] is True
    assert contract["invariants"]["existing_domain_transition_gates_remain_authoritative"] is True
    assert contract["invariants"]["operations_view_does_not_mutate_data"] is True


def test_admin_operations_endpoint_is_read_only_snapshot(monkeypatch) -> None:
    expected = {"version": "MARKORBIT_OPERATIONS_V2", "operations": []}
    calls = []

    def fake_snapshot(*, package_limit, task_limit):
        calls.append((package_limit, task_limit))
        return expected

    monkeypatch.setattr(admin_system_api, "operations_snapshot", fake_snapshot)

    assert admin_system_api.admin_operations_snapshot(package_limit=25, task_limit=10) == expected
    assert calls == [(25, 10)]
