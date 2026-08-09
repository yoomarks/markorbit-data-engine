from __future__ import annotations

from pathlib import Path
from typing import Any

from app.us_assignment.audit_real_data import build_audit
from app.us_assignment.repository import list_assignment_packages


READINESS_VERSION = "US_ASSIGNMENT_READINESS_V1"


def _action(code: str, description: str, command: str | None = None) -> dict[str, Any]:
    return {"code": code, "description": description, "command": command}


def evaluate_readiness(
    *,
    packages: list[dict[str, Any]],
    acceptance: dict[str, Any],
    verify_sources: bool,
) -> dict[str, Any]:
    common = {
        "readiness_version": READINESS_VERSION,
        "verify_sources": verify_sources,
        "legal_ownership_conclusion": False,
    }
    if not packages:
        return {
            **common,
            "state": "SOURCE_NOT_REGISTERED",
            "ready": False,
            "reason_codes": ["no_assignment_packages_registered"],
            "next_action": _action(
                "REGISTER_ASSIGNMENT_SOURCE",
                "Register an authoritative local USPTO assignment XML/ZIP with explicit effective date and source kind.",
                ".\\scripts\\register-us-assignment.ps1",
            ),
        }

    status = str(acceptance.get("status") or "")
    if status == "PASS":
        return {
            **common,
            "state": "ACCEPTED",
            "ready": True,
            "reason_codes": [],
            "next_action": _action("NONE", "Assignment fact corpus is source-backed accepted."),
        }
    if status == "PASS_WITH_WARNINGS":
        warnings = list(acceptance.get("warning_reasons") or [])
        if warnings == ["assignment_source_sha_verification_not_requested"]:
            return {
                **common,
                "state": "SOURCE_VERIFICATION_REQUIRED",
                "ready": False,
                "reason_codes": warnings,
                "next_action": _action(
                    "RUN_SOURCE_BACKED_ASSIGNMENT_ACCEPTANCE",
                    "Verify local authoritative Assignment source SHA-256 evidence.",
                    ".\\scripts\\audit-us-assignment-real-data.ps1 -VerifySourceFiles",
                ),
            }
        return {
            **common,
            "state": "ACCEPTED_WITH_DATA_WARNINGS",
            "ready": True,
            "reason_codes": warnings,
            "next_action": _action(
                "REVIEW_DATA_WARNINGS",
                "Review non-fatal Assignment data-quality warnings before downstream reconciliation.",
            ),
        }
    if status == "NOT_READY":
        reasons = list(acceptance.get("not_ready_reasons") or [])
        if "assignment_ingestion_not_complete" in reasons:
            action = _action(
                "RUN_ASSIGNMENT_INGESTION",
                "Process the next registered Assignment package.",
                ".\\scripts\\run-us-assignment.ps1",
            )
        elif "successful_assignment_packages_require_m10_replay" in reasons:
            action = _action(
                "REPLAY_ASSIGNMENT_PACKAGES",
                "Re-register/replay legacy Assignment packages under US_ASSIGNMENT_M1.0.",
                ".\\scripts\\retry-us-assignment.ps1",
            )
        else:
            action = _action(
                "INVESTIGATE_ASSIGNMENT_READINESS",
                "Inspect Assignment schema/package readiness before mutation.",
            )
        return {
            **common,
            "state": "NOT_READY",
            "ready": False,
            "reason_codes": reasons,
            "next_action": action,
        }
    return {
        **common,
        "state": "FAILED",
        "ready": False,
        "reason_codes": list(acceptance.get("hard_fail_reasons") or [])
        or [f"unexpected_acceptance_status:{status}"],
        "next_action": _action(
            "INVESTIGATE_ASSIGNMENT_ACCEPTANCE_FAILURE",
            "Assignment durable/source integrity failed; do not infer ownership or auto-reset.",
        ),
    }


def build_readiness(*, raw_root: Path, verify_sources: bool = False) -> dict[str, Any]:
    packages = list_assignment_packages()
    acceptance = build_audit(raw_root=raw_root, verify_sources=verify_sources)
    result = evaluate_readiness(
        packages=packages,
        acceptance=acceptance,
        verify_sources=verify_sources,
    )
    result["acceptance"] = acceptance
    return result
