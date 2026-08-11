from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.us_ttab.audit_real_data import build_audit
from app.us_ttab.corpus_manifest import preflight_manifest
from app.us_ttab.repository import list_ttab_packages


AUDIT_VERSION = "US_TTAB_MANIFEST_ACCEPTANCE_V1"


def build_manifest_acceptance(manifest_path: Path, raw_root: Path) -> dict[str, Any]:
    preflight = preflight_manifest(manifest_path, raw_root)
    base = build_audit(raw_root=raw_root, verify_sources=False)
    hard = list(base.get("hard_fail_reasons") or [])
    not_ready = list(base.get("not_ready_reasons") or [])
    warnings = [
        item
        for item in (base.get("warning_reasons") or [])
        if item != "ttab_source_sha_verification_not_requested"
    ]

    if not preflight.get("safe"):
        not_ready.append("ttab_manifest_source_preflight_not_ready")

    manifest_by_sha = {
        str(item["sha256"]).lower(): item for item in preflight.get("plan", [])
    }
    packages = list_ttab_packages()
    registry_by_sha = {
        str(row.get("sha256") or "").lower(): row for row in packages
    }
    missing_registry = sorted(set(manifest_by_sha) - set(registry_by_sha))
    extra_registry = sorted(set(registry_by_sha) - set(manifest_by_sha))
    metadata_mismatches: list[dict[str, Any]] = []

    for digest in sorted(set(manifest_by_sha) & set(registry_by_sha)):
        source = manifest_by_sha[digest]
        row = registry_by_sha[digest]
        mismatch: dict[str, Any] = {"sha256": digest}
        if str(row.get("package_kind") or "") != str(source["source_kind"]):
            mismatch["source_kind"] = {
                "manifest": source["source_kind"],
                "registered": row.get("package_kind"),
            }
        if str(row.get("partition_value") or "") != str(source["snapshot_at"]):
            mismatch["snapshot_at"] = {
                "manifest": source["snapshot_at"],
                "registered": str(row.get("partition_value") or ""),
            }
        if len(mismatch) > 1:
            metadata_mismatches.append(mismatch)

    if missing_registry:
        not_ready.append("ttab_manifest_sources_not_registered")
    if extra_registry:
        hard.append("ttab_registry_contains_sources_outside_manifest")
    if metadata_mismatches:
        hard.append("ttab_manifest_registry_metadata_mismatch")

    success_shas = {
        str(row.get("sha256") or "").lower()
        for row in packages
        if str(row.get("status") or "") == "SUCCESS"
    }
    incomplete_shas = sorted(set(manifest_by_sha) - success_shas)
    if preflight.get("safe") and incomplete_shas:
        not_ready.append("ttab_manifest_replay_not_complete")

    hard = list(dict.fromkeys(hard))
    not_ready = list(dict.fromkeys(not_ready))
    warnings = list(dict.fromkeys(warnings))
    if hard:
        status = "FAIL"
    elif not_ready:
        status = "NOT_READY"
    elif warnings:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"

    return {
        "audit": AUDIT_VERSION,
        "status": status,
        "hard_fail_reasons": hard,
        "not_ready_reasons": not_ready,
        "warning_reasons": warnings,
        "manifest_preflight": preflight,
        "manifest_registry": {
            "manifest_source_count": len(manifest_by_sha),
            "registered_source_count": len(registry_by_sha),
            "successful_manifest_source_count": len(set(manifest_by_sha) & success_shas),
            "missing_registry_sha256": missing_registry,
            "extra_registry_sha256": extra_registry,
            "incomplete_sha256": incomplete_shas,
            "metadata_mismatches": metadata_mismatches,
        },
        "database_acceptance": base,
        "source_verification": "MANIFEST_PREFLIGHT_SHA256_ALL_SOURCES",
        "semantics": "USPTO_TTAB_PROCEDURAL_FACTS_NOT_OUTCOME_OR_SUBSTANTIVE_RIGHTS_CONCLUSION",
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit full USPTO TTAB bulk replay against the explicit source manifest"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    report = build_manifest_acceptance(
        args.manifest,
        get_settings().raw_data_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] in {"PASS", "PASS_WITH_WARNINGS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
