from __future__ import annotations

from app.us.pipeline_readiness import evaluate_readiness


N = 5
SAFE_SOURCE = {"safe_to_replay": True, "status": "PASS"}
READY_SCHEMA = {"ready": True, "postgres": "US_M1.3", "clickhouse": ["US_M1.3"]}


def _evaluate(**overrides):
    values = {
        "expected_history_parts": N,
        "preflight": SAFE_SOURCE,
        "schema": READY_SCHEMA,
        "replay": {"status": "READY", "blockers": []},
        "reset": None,
        "acceptance": None,
        "verify_source_files": False,
    }
    values.update(overrides)
    return evaluate_readiness(**values)


def test_source_corpus_blocked_is_always_first_and_nonmutating() -> None:
    result = _evaluate(
        preflight={
            "safe_to_replay": False,
            "hard_issue_types": ["SEMANTIC_PARTITION_SHA_CONFLICT"],
            "not_ready_reasons": ["historical_part_sequence_incomplete"],
        },
        schema={"ready": False},
        replay={"status": "BLOCKED", "blockers": ["anything"]},
    )
    assert result["state"] == "SOURCE_CORPUS_BLOCKED"
    assert result["ready"] is False
    assert result["next_action"]["code"] == "FIX_SOURCE_CORPUS"
    assert result["next_action"]["mutates"] is False
    assert "-DeepSourceTest" in result["next_action"]["command"]


def test_schema_is_next_after_safe_sources() -> None:
    result = _evaluate(schema={"ready": False})
    assert result["state"] == "SCHEMA_NOT_READY"
    assert result["next_action"]["code"] == "APPLY_US_SCHEMA"
    assert result["next_action"]["mutates"] is True
    assert "apply-us-m1-schema.ps1" in result["next_action"]["command"]


def test_ready_replay_routes_to_dry_run_not_apply() -> None:
    result = _evaluate()
    assert result["state"] == "REPLAY_READY"
    assert result["next_action"]["code"] == "RUN_REPLAY_DRY_RUN"
    assert result["next_action"]["mutates"] is False
    command = result["next_action"]["command"]
    assert "replay-us-deterministic.ps1" in command
    assert "-Apply" not in command
    assert "-All" not in command


def test_pending_archive_source_routes_to_staging_dry_run() -> None:
    result = _evaluate(
        replay={
            "status": "BLOCKED",
            "blockers": ["pending_source_requires_archive_staging"],
        }
    )
    assert result["state"] == "STAGING_REQUIRED"
    assert result["next_action"]["code"] == "RUN_STAGING_DRY_RUN"
    assert "stage-us-replay-sources.ps1" in result["next_action"]["command"]
    assert "-Apply" not in result["next_action"]["command"]


def test_stale_success_needing_reset_but_archive_only_routes_to_staging_first() -> None:
    result = _evaluate(
        replay={
            "status": "BLOCKED",
            "blockers": ["successful_package_requires_m13_replay"],
        },
        reset={
            "status": "BLOCKED",
            "blockers": ["archive_sources_must_be_staged_before_reset"],
        },
    )
    assert result["state"] == "STAGING_REQUIRED_FOR_CLEAN_REBUILD"
    assert result["next_action"]["code"] == "RUN_STAGING_DRY_RUN"


def test_only_reset_recoverable_replay_blockers_route_to_reset_dry_run() -> None:
    result = _evaluate(
        replay={
            "status": "BLOCKED",
            "blockers": [
                "successful_package_requires_m13_replay",
                "registered_source_rank_order_violation",
            ],
        },
        reset={"status": "READY", "blockers": []},
    )
    assert result["state"] == "CLEAN_REBUILD_REQUIRED"
    assert result["next_action"]["code"] == "RUN_CLEAN_RESET_DRY_RUN"
    assert result["next_action"]["destructive"] is False
    command = result["next_action"]["command"]
    assert "reset-us-clean-rebuild.ps1" in command
    assert "-Apply" not in command
    assert "RESET-US-M1.3" not in command


def test_unrecognized_replay_integrity_blocker_never_auto_routes_to_reset() -> None:
    result = _evaluate(
        replay={
            "status": "BLOCKED",
            "blockers": ["registry_source_identity_mismatch"],
        },
        reset={"status": "READY", "blockers": []},
    )
    assert result["state"] == "PIPELINE_BLOCKED"
    assert result["next_action"]["code"] == "INVESTIGATE_REPLAY_BLOCKERS"
    assert "reset-us-clean-rebuild.ps1" not in result["next_action"]["command"]


def test_complete_replay_without_acceptance_report_routes_to_acceptance() -> None:
    result = _evaluate(replay={"status": "COMPLETE", "blockers": []})
    assert result["state"] == "ACCEPTANCE_REQUIRED"
    assert result["next_action"]["code"] == "RUN_SOURCE_BACKED_ACCEPTANCE"
    assert "-VerifySourceFiles" in result["next_action"]["command"]


def test_database_only_acceptance_routes_to_source_verification() -> None:
    result = _evaluate(
        replay={"status": "COMPLETE", "blockers": []},
        acceptance={
            "status": "PASS_WITH_WARNINGS",
            "warning_reasons": ["source_sha_verification_not_requested"],
        },
    )
    assert result["state"] == "SOURCE_VERIFICATION_REQUIRED"
    assert result["ready"] is False
    assert "-VerifySourceFiles" in result["next_action"]["command"]


def test_source_backed_pass_is_terminal_accepted() -> None:
    result = _evaluate(
        replay={"status": "COMPLETE", "blockers": []},
        acceptance={"status": "PASS"},
        verify_source_files=True,
    )
    assert result["state"] == "ACCEPTED"
    assert result["ready"] is True
    assert result["next_action"]["code"] == "NONE"
    assert result["next_action"]["command"] is None


def test_acceptance_not_ready_does_not_route_to_reset() -> None:
    result = _evaluate(
        replay={"status": "COMPLETE", "blockers": []},
        acceptance={
            "status": "NOT_READY",
            "not_ready_reasons": ["daily_update_not_successful"],
        },
    )
    assert result["state"] == "ACCEPTANCE_NOT_READY"
    assert result["next_action"]["code"] == "INVESTIGATE_ACCEPTANCE_READINESS"
    assert "reset-us-clean-rebuild.ps1" not in result["next_action"]["command"]


def test_acceptance_failure_is_investigation_only_never_reset() -> None:
    result = _evaluate(
        replay={"status": "COMPLETE", "blockers": []},
        acceptance={
            "status": "FAIL",
            "hard_fail_reasons": ["source_lineage_rank_mismatch"],
        },
    )
    assert result["state"] == "ACCEPTANCE_FAILED"
    assert result["next_action"]["code"] == "INVESTIGATE_ACCEPTANCE_FAILURE"
    assert "reset-us-clean-rebuild.ps1" not in result["next_action"]["command"]


def test_expected_history_parts_must_be_positive() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least 1"):
        evaluate_readiness(
            expected_history_parts=0,
            preflight=SAFE_SOURCE,
        )
