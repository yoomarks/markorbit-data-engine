from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.us import audit_real_data as audit_base
from app.us.audit_real_data_v2 import build_audit as build_acceptance_audit
from app.us.migrations import US_SCHEMA_VERSION
from app.us.replay_executor import build_replay_plan
from app.us.reset_rebuild import build_reset_plan
from app.us.source_preflight import build_preflight


READINESS_VERSION = "US_PIPELINE_READINESS_V1"
RESET_RECOVERABLE_REPLAY_BLOCKERS = {
    "successful_package_requires_m13_replay",
    "out_of_order_success_package",
    "registered_source_rank_order_violation",
    "unknown_registry_status",
}


def _command(script: str, expected_history_parts: int, *extra: str) -> str:
    parts = [
        "powershell.exe",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        f".\\scripts\\{script}",
        "-ExpectedHistoryParts",
        str(expected_history_parts),
        *extra,
    ]
    return " ".join(parts)


def _action(
    code: str,
    description: str,
    *,
    command: str | None = None,
    mutates: bool = False,
    destructive: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "description": description,
        "command": command,
        "mutates": mutates,
        "destructive": destructive,
    }


def schema_state() -> dict[str, Any]:
    postgres_version = audit_base._postgres_schema_version()
    clickhouse_versions = audit_base._clickhouse_schema_versions()
    return {
        "expected": US_SCHEMA_VERSION,
        "postgres": postgres_version,
        "clickhouse": clickhouse_versions,
        "ready": (
            postgres_version == US_SCHEMA_VERSION
            and US_SCHEMA_VERSION in clickhouse_versions
        ),
    }


def evaluate_readiness(
    *,
    expected_history_parts: int,
    preflight: dict[str, Any],
    schema: dict[str, Any] | None = None,
    replay: dict[str, Any] | None = None,
    reset: dict[str, Any] | None = None,
    acceptance: dict[str, Any] | None = None,
    verify_source_files: bool = False,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    common = {
        "readiness_version": READINESS_VERSION,
        "expected_history_parts": expected_history_parts,
        "verify_source_files": verify_source_files,
    }

    if not preflight.get("safe_to_replay"):
        return {
            **common,
            "state": "SOURCE_CORPUS_BLOCKED",
            "ready": False,
            "reason_codes": list(preflight.get("hard_issue_types") or [])
            + list(preflight.get("not_ready_reasons") or []),
            "next_action": _action(
                "FIX_SOURCE_CORPUS",
                "Resolve source-preflight blockers before any database mutation.",
                command=_command(
                    "preflight-us-source-replay.ps1",
                    expected_history_parts,
                    "-DeepSourceTest",
                ),
            ),
        }

    if schema is None or not schema.get("ready"):
        return {
            **common,
            "state": "SCHEMA_NOT_READY",
            "ready": False,
            "reason_codes": ["us_m13_schema_not_ready"],
            "next_action": _action(
                "APPLY_US_SCHEMA",
                "Apply the additive/idempotent US M1.3 schema before replay planning.",
                command=(
                    "powershell.exe -ExecutionPolicy Bypass -File "
                    ".\\scripts\\apply-us-m1-schema.ps1"
                ),
                mutates=True,
            ),
        }

    if replay is None:
        return {
            **common,
            "state": "PIPELINE_STATE_INCOMPLETE",
            "ready": False,
            "reason_codes": ["replay_plan_missing"],
            "next_action": _action(
                "RECHECK_PIPELINE",
                "Re-run the read-only pipeline readiness command.",
            ),
        }

    replay_status = str(replay.get("status") or "")
    replay_blockers = set(str(value) for value in replay.get("blockers") or [])

    if replay_status == "READY":
        return {
            **common,
            "state": "REPLAY_READY",
            "ready": False,
            "reason_codes": [],
            "next_action": _action(
                "RUN_REPLAY_DRY_RUN",
                "Review the deterministic next package before applying replay.",
                command=_command(
                    "replay-us-deterministic.ps1",
                    expected_history_parts,
                    "-DeepSourceTest",
                ),
            ),
        }

    if replay_status == "BLOCKED":
        if "pending_source_requires_archive_staging" in replay_blockers:
            return {
                **common,
                "state": "STAGING_REQUIRED",
                "ready": False,
                "reason_codes": sorted(replay_blockers),
                "next_action": _action(
                    "RUN_STAGING_DRY_RUN",
                    "Stage authoritative archive-only pending sources before replay.",
                    command=_command(
                        "stage-us-replay-sources.ps1",
                        expected_history_parts,
                        "-DeepSourceTest",
                    ),
                ),
            }

        if reset is not None:
            reset_status = str(reset.get("status") or "")
            reset_blockers = set(str(value) for value in reset.get("blockers") or [])
            if (
                "archive_sources_must_be_staged_before_reset" in reset_blockers
                and replay_blockers & RESET_RECOVERABLE_REPLAY_BLOCKERS
            ):
                return {
                    **common,
                    "state": "STAGING_REQUIRED_FOR_CLEAN_REBUILD",
                    "ready": False,
                    "reason_codes": sorted(replay_blockers | reset_blockers),
                    "next_action": _action(
                        "RUN_STAGING_DRY_RUN",
                        "Stage the accepted source set before considering a clean rebuild reset.",
                        command=_command(
                            "stage-us-replay-sources.ps1",
                            expected_history_parts,
                            "-DeepSourceTest",
                        ),
                    ),
                }
            if (
                reset_status == "READY"
                and replay_blockers
                and replay_blockers <= RESET_RECOVERABLE_REPLAY_BLOCKERS
            ):
                return {
                    **common,
                    "state": "CLEAN_REBUILD_REQUIRED",
                    "ready": False,
                    "reason_codes": sorted(replay_blockers),
                    "next_action": _action(
                        "RUN_CLEAN_RESET_DRY_RUN",
                        "Review the guarded US-only clean-rebuild reset plan; do not apply automatically.",
                        command=_command(
                            "reset-us-clean-rebuild.ps1",
                            expected_history_parts,
                            "-DeepSourceTest",
                        ),
                        destructive=False,
                    ),
                }

        return {
            **common,
            "state": "PIPELINE_BLOCKED",
            "ready": False,
            "reason_codes": sorted(replay_blockers),
            "next_action": _action(
                "INVESTIGATE_REPLAY_BLOCKERS",
                "Investigate replay/registry integrity; no automatic reset is recommended.",
                command=_command(
                    "replay-us-deterministic.ps1",
                    expected_history_parts,
                    "-DeepSourceTest",
                ),
            ),
        }

    if replay_status != "COMPLETE":
        return {
            **common,
            "state": "PIPELINE_BLOCKED",
            "ready": False,
            "reason_codes": [f"unexpected_replay_status:{replay_status}"],
            "next_action": _action(
                "INVESTIGATE_REPLAY_STATUS",
                "Investigate the unexpected replay planner state.",
            ),
        }

    if acceptance is None:
        return {
            **common,
            "state": "ACCEPTANCE_REQUIRED",
            "ready": False,
            "reason_codes": ["acceptance_report_missing"],
            "next_action": _action(
                "RUN_SOURCE_BACKED_ACCEPTANCE",
                "Run the final read-only source-backed acceptance audit.",
                command=_command(
                    "audit-us-real-data.ps1",
                    expected_history_parts,
                    "-VerifySourceFiles",
                ),
            ),
        }

    acceptance_status = str(acceptance.get("status") or "")
    if acceptance_status == "PASS":
        return {
            **common,
            "state": "ACCEPTED",
            "ready": True,
            "reason_codes": [],
            "next_action": _action(
                "NONE",
                "US M1.3 corpus is source-backed accepted; no pipeline action is required.",
            ),
        }

    if acceptance_status == "PASS_WITH_WARNINGS":
        return {
            **common,
            "state": "SOURCE_VERIFICATION_REQUIRED",
            "ready": False,
            "reason_codes": list(acceptance.get("warning_reasons") or []),
            "next_action": _action(
                "RUN_SOURCE_BACKED_ACCEPTANCE",
                "Database acceptance passed; verify authoritative source ZIP/XML SHA-256 evidence.",
                command=_command(
                    "audit-us-real-data.ps1",
                    expected_history_parts,
                    "-VerifySourceFiles",
                ),
            ),
        }

    if acceptance_status == "NOT_READY":
        return {
            **common,
            "state": "ACCEPTANCE_NOT_READY",
            "ready": False,
            "reason_codes": list(acceptance.get("not_ready_reasons") or []),
            "next_action": _action(
                "INVESTIGATE_ACCEPTANCE_READINESS",
                "Acceptance evidence is incomplete; inspect the reported reasons before mutation.",
                command=_command(
                    "audit-us-real-data.ps1",
                    expected_history_parts,
                    "-VerifySourceFiles",
                ),
            ),
        }

    return {
        **common,
        "state": "ACCEPTANCE_FAILED",
        "ready": False,
        "reason_codes": list(acceptance.get("hard_fail_reasons") or [])
        or [f"unexpected_acceptance_status:{acceptance_status}"],
        "next_action": _action(
            "INVESTIGATE_ACCEPTANCE_FAILURE",
            "Acceptance found durable integrity/source evidence failures; do not reset automatically.",
            command=_command(
                "audit-us-real-data.ps1",
                expected_history_parts,
                "-VerifySourceFiles",
            ),
        ),
    }


def build_readiness(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
    verify_source_files: bool = False,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    preflight = build_preflight(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
    )
    reports: dict[str, Any] = {"preflight": preflight}
    if not preflight.get("safe_to_replay"):
        decision = evaluate_readiness(
            expected_history_parts=expected_history_parts,
            preflight=preflight,
            verify_source_files=verify_source_files,
        )
        return {**decision, "reports": reports}

    schema = schema_state()
    reports["schema"] = schema
    if not schema["ready"]:
        decision = evaluate_readiness(
            expected_history_parts=expected_history_parts,
            preflight=preflight,
            schema=schema,
            verify_source_files=verify_source_files,
        )
        return {**decision, "reports": reports}

    replay = build_replay_plan(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
    )
    reports["replay"] = replay

    reset: dict[str, Any] | None = None
    if replay.get("status") == "BLOCKED":
        reset = build_reset_plan(
            raw_root,
            expected_history_parts=expected_history_parts,
            deep_source_test=deep_source_test,
        )
        reports["reset"] = reset

    acceptance: dict[str, Any] | None = None
    if replay.get("status") == "COMPLETE":
        acceptance = build_acceptance_audit(
            verify_source_files=verify_source_files,
            expected_history_parts=expected_history_parts,
        )
        reports["acceptance"] = acceptance

    decision = evaluate_readiness(
        expected_history_parts=expected_history_parts,
        preflight=preflight,
        schema=schema,
        replay=replay,
        reset=reset,
        acceptance=acceptance,
        verify_source_files=verify_source_files,
    )
    return {**decision, "reports": reports}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only US M1.3 pipeline readiness and next-action router"
    )
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--deep-source-test", action="store_true")
    parser.add_argument("--verify-source-files", action="store_true")
    args = parser.parse_args()
    if args.expected_history_parts < 1:
        parser.error("--expected-history-parts must be at least 1")

    report = build_readiness(
        get_settings().raw_data_root,
        expected_history_parts=args.expected_history_parts,
        deep_source_test=args.deep_source_test,
        verify_source_files=args.verify_source_files,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
