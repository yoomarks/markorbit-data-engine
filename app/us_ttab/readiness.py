from __future__ import annotations

from pathlib import Path
from typing import Any

from app.us_ttab.audit_real_data import build_audit
from app.us_ttab.repository import list_ttab_packages


READINESS_VERSION = "US_TTAB_READINESS_V1"


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
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }
    if not packages:
        return {
            **common,
            "state": "SOURCE_NOT_REGISTERED",
            "ready": False,
            "reason_codes": ["no_ttab_packages_registered"],
            "next_action": _action(
                "REGISTER_TTAB_SNAPSHOT",
                "Register an authoritative local TTABVUE proceeding XML/ZIP with explicit snapshot timestamp.",
                ".\\scripts\\register-us-ttab.ps1",
            ),
        }

    status = str(acceptance.get("status") or "")
    if status == "PASS":
        return {
            **common,
            "state": "ACCEPTED",
            "ready": True,
            "reason_codes": [],
            "next_action": _action("NONE", "TTAB procedural fact corpus is source-backed accepted."),
        }
    if status == "PASS_WITH_WARNINGS":
        warnings = list(acceptance.get("warning_reasons") or [])
        if warnings == ["ttab_source_sha_verification_not_requested"]:
            return {
                **common,
                "state": "SOURCE_VERIFICATION_REQUIRED",
                "ready": False,
                "reason_codes": warnings,
                "next_action": _action(
                    "RUN_SOURCE_BACKED_TTAB_ACCEPTANCE",
                    "Verify local authoritative TTAB snapshot SHA-256 evidence.",
                    ".\\scripts\\audit-us-ttab-real-data.ps1 -VerifySourceFiles",
                ),
            }
        return {
            **common,
            "state": "ACCEPTED_WITH_DATA_WARNINGS",
            "ready": True,
            "reason_codes": warnings,
            "next_action": _action(
                "REVIEW_TTAB_DATA_WARNINGS",
                "Review non-fatal TTAB source/data coverage warnings.",
            ),
        }
    if status == "NOT_READY":
        reasons = list(acceptance.get("not_ready_reasons") or [])
        if "ttab_ingestion_not_complete" in reasons:
            action = _action(
                "RUN_TTAB_INGESTION",
                "Process the next registered TTAB snapshot package.",
                ".\\scripts\\run-us-ttab.ps1",
            )
        elif "successful_ttab_packages_require_m10_replay" in reasons:
            action = _action(
                "REPLAY_TTAB_PACKAGES",
                "Replay legacy TTAB packages under US_TTAB_M1.0.",
                ".\\scripts\\retry-us-ttab.ps1",
            )
        else:
            action = _action(
                "INVESTIGATE_TTAB_READINESS",
                "Inspect TTAB schema/package readiness before mutation.",
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
            "INVESTIGATE_TTAB_ACCEPTANCE_FAILURE",
            "TTAB durable/source integrity failed; do not infer deadline validity or legal outcome.",
        ),
    }


def build_readiness(*, raw_root: Path, verify_sources: bool = False) -> dict[str, Any]:
    packages = list_ttab_packages()
    acceptance = build_audit(raw_root=raw_root, verify_sources=verify_sources)
    result = evaluate_readiness(
        packages=packages,
        acceptance=acceptance,
        verify_sources=verify_sources,
    )
    result["acceptance"] = acceptance
    return result
