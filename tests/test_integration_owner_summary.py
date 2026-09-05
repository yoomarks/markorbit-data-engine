from datetime import datetime, timezone

from app import integration_owner_summary as subject
from app.integration_g0_contract import g0_contract_descriptor

# Regression guards keep the owner projection bounded and contract-synchronized.


def test_owner_summary_projects_only_bounded_aggregates(monkeypatch):
    generated_at = datetime(2026, 9, 5, 8, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(
        subject,
        "health",
        lambda: {
            "api": "ok",
            "postgres": "error: secret database detail",
            "clickhouse": "ok",
        },
    )
    monkeypatch.setattr(
        subject,
        "operations_snapshot",
        lambda: {
            "version": "MARKORBIT_OPERATIONS_V2",
            "action_authority": "ADVISORY_ONLY_EXISTING_DOMAIN_GATES_AND_CHECKPOINT_VALIDATORS_REMAIN_AUTHORITATIVE",
            "summary": {
                "operation_count": 7,
                "state_counts": {"RUNNING": 1, "BLOCKED": 2},
                "resume_candidates": 1,
                "retry_candidates": 1,
                "operator_required": 2,
                "partial_state_preservation_required": 3,
            },
            "operations": [
                {
                    "owner_id": "must-not-leak",
                    "details": {"file_name": "secret.zip", "latest_job_error": "boom"},
                    "next_safe_action": "MUTATE_SOMETHING",
                }
            ],
        },
    )
    monkeypatch.setattr(
        subject,
        "domain_progress_snapshot",
        lambda: {
            "version": "MARKORBIT_ADMIN_PROGRESS_V2",
            "read_only": True,
            "generated_at": generated_at,
            "active_count": 1,
            "items": [
                {
                    "run_id": "must-not-leak",
                    "current_package": {"file_name": "secret.zip"},
                    "detail": {"current_subtask": {"last_error": "boom"}},
                    "live_metrics": {"eta_seconds": 123},
                }
            ],
        },
    )

    result = subject.owner_summary()

    assert result["authority"] == "DATA_ENGINE_FACT_READ_MODEL"
    assert result["read_only"] is True
    assert result["generated_at"] == generated_at
    assert result["health"] == {"status": "degraded"}
    assert result["operations"] == {
        "version": "MARKORBIT_OPERATIONS_V2",
        "action_authority": "ADVISORY_ONLY_EXISTING_DOMAIN_GATES_AND_CHECKPOINT_VALIDATORS_REMAIN_AUTHORITATIVE",
        "summary": {
            "operation_count": 7,
            "state_counts": {"RUNNING": 1, "BLOCKED": 2},
            "resume_candidates": 1,
            "retry_candidates": 1,
            "operator_required": 2,
            "partial_state_preservation_required": 3,
        },
    }
    assert result["domain_progress"] == {
        "version": "MARKORBIT_ADMIN_PROGRESS_V2",
        "active_count": 1,
    }

    rendered = repr(result)
    for forbidden in (
        "must-not-leak",
        "secret.zip",
        "secret database detail",
        "latest_job_error",
        "next_safe_action",
        "current_subtask",
        "live_metrics",
        "eta_seconds",
    ):
        assert forbidden not in rendered


def test_owner_summary_resource_is_additive_read_only_contract():
    resources = g0_contract_descriptor()["query_contract"]["resources"]
    resource = next(item for item in resources if item["path"] == "/api/v1/owner-summary")

    assert resource == {
        "path": "/api/v1/owner-summary",
        "query": {},
        "pagination": "none",
        "semantics": "bounded_owner_local_aggregate_projection",
        "read_only": True,
        "admin_detail_exposed": False,
    }
