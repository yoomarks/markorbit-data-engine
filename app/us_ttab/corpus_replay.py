from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.us_ttab import TTAB_SCHEMA_VERSION
from app.us_ttab.corpus_manifest import preflight_manifest
from app.us_ttab.jobs import run_ttab_once
from app.us_ttab.repository import (
    list_ttab_packages,
    normalize_snapshot_at,
    register_ttab_source,
)


REPLAY_VERSION = "US_TTAB_MANIFEST_REPLAY_V1"
_ALLOWED_STATUSES = {
    "REGISTERED",
    "PROCESSING",
    "INTERRUPTED",
    "FAILED",
    "MISSING_FILE",
    "SUCCESS",
}
_RETRY_STATUSES = {"PROCESSING", "INTERRUPTED", "FAILED", "MISSING_FILE"}


def _canonical_snapshot_text(value: object) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    normalized = normalize_snapshot_at(parsed)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _registry_state(preflight: dict[str, Any]) -> dict[str, Any]:
    packages = list_ttab_packages()
    by_sha = {str(row.get("sha256") or "").lower(): row for row in packages}
    manifest_shas = {str(item["sha256"]).lower() for item in preflight.get("plan", [])}
    blockers: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    for row in packages:
        digest = str(row.get("sha256") or "").lower()
        if digest not in manifest_shas:
            blockers.append(
                {
                    "type": "REGISTRY_PACKAGE_OUTSIDE_MANIFEST",
                    "package_id": str(row["package_id"]),
                    "file_name": str(row["file_name"]),
                    "status": str(row["status"]),
                    "sha256": digest,
                }
            )

    first_unfinished: int | None = None
    for index, item in enumerate(preflight.get("plan", [])):
        digest = str(item["sha256"]).lower()
        row = by_sha.get(digest)
        if row is None:
            action = "REGISTER_AND_INGEST"
            status = "UNREGISTERED"
            package_id = None
        else:
            status = str(row.get("status") or "")
            package_id = str(row["package_id"])
            if status not in _ALLOWED_STATUSES:
                blockers.append(
                    {
                        "type": "UNKNOWN_REGISTRY_STATUS",
                        "package_id": package_id,
                        "status": status,
                    }
                )
            if str(row.get("package_kind") or "") != str(item["source_kind"]):
                blockers.append(
                    {
                        "type": "REGISTRY_SOURCE_KIND_MISMATCH",
                        "package_id": package_id,
                        "manifest": item["source_kind"],
                        "registered": row.get("package_kind"),
                    }
                )
            if str(row.get("partition_value") or "") != str(item["snapshot_at"]):
                blockers.append(
                    {
                        "type": "REGISTRY_SNAPSHOT_AT_MISMATCH",
                        "package_id": package_id,
                        "manifest": item["snapshot_at"],
                        "registered": str(row.get("partition_value") or ""),
                    }
                )
            if status == "SUCCESS":
                profile = row.get("profile") or {}
                if str(row.get("schema_version") or "") != TTAB_SCHEMA_VERSION:
                    blockers.append(
                        {
                            "type": "SUCCESS_PACKAGE_SCHEMA_VERSION_MISMATCH",
                            "package_id": package_id,
                        }
                    )
                if str(profile.get("source_sha256") or "").lower() != digest:
                    blockers.append(
                        {
                            "type": "SUCCESS_PACKAGE_SOURCE_SHA_MISMATCH",
                            "package_id": package_id,
                        }
                    )
                try:
                    profile_snapshot = _canonical_snapshot_text(profile.get("snapshot_at"))
                except Exception:
                    profile_snapshot = ""
                if profile_snapshot != str(item["snapshot_at"]):
                    blockers.append(
                        {
                            "type": "SUCCESS_PACKAGE_PROFILE_SNAPSHOT_MISMATCH",
                            "package_id": package_id,
                        }
                    )
                action = "SKIP_SUCCESS"
            elif status in _RETRY_STATUSES:
                action = "RETRY_FULL_PACKAGE"
            else:
                action = "INGEST"

        if action != "SKIP_SUCCESS" and first_unfinished is None:
            first_unfinished = index
        if action == "SKIP_SUCCESS" and first_unfinished is not None:
            blockers.append(
                {
                    "type": "OUT_OF_ORDER_SUCCESS_PACKAGE",
                    "file_name": item["file_name"],
                    "index": index,
                }
            )
        if (
            first_unfinished is not None
            and index > first_unfinished
            and action == "RETRY_FULL_PACKAGE"
        ):
            blockers.append(
                {
                    "type": "OUT_OF_ORDER_FAILED_PACKAGE",
                    "file_name": item["file_name"],
                    "index": index,
                }
            )

        actions.append(
            {
                **item,
                "package_id": package_id,
                "registry_status": status,
                "action": action,
            }
        )

    remaining = [item for item in actions if item["action"] != "SKIP_SUCCESS"]
    return {
        "actions": actions,
        "remaining": remaining,
        "remaining_count": len(remaining),
        "blockers": blockers,
    }


def build_replay_plan(manifest_path: Path, raw_root: Path) -> dict[str, Any]:
    preflight = preflight_manifest(manifest_path, raw_root)
    if not preflight.get("safe"):
        return {
            "replay_version": REPLAY_VERSION,
            "status": "BLOCKED",
            "preflight": preflight,
            "blockers": [{"type": "SOURCE_PREFLIGHT_NOT_READY"}],
            "actions": [],
            "remaining_count": 0,
        }
    state = _registry_state(preflight)
    if state["blockers"]:
        status = "BLOCKED"
    elif not state["remaining"]:
        status = "COMPLETE"
    elif state["remaining"][0]["action"] == "RETRY_FULL_PACKAGE":
        status = "RETRY_REQUIRED"
    else:
        status = "READY"
    return {
        "replay_version": REPLAY_VERSION,
        "status": status,
        "preflight": preflight,
        "blockers": state["blockers"],
        "actions": state["actions"],
        "remaining_count": state["remaining_count"],
        "next_action": state["remaining"][0] if state["remaining"] else None,
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


def execute_replay(
    manifest_path: Path,
    raw_root: Path,
    *,
    apply: bool = False,
    all_packages: bool = False,
    max_packages: int = 1,
    resume_failed: bool = False,
) -> dict[str, Any]:
    if max_packages < 1:
        raise ValueError("max_packages must be positive")
    initial = build_replay_plan(manifest_path, raw_root)
    if initial["status"] in {"BLOCKED", "COMPLETE"}:
        return {"mode": "APPLY" if apply else "DRY_RUN", **initial, "processed_count": 0}
    if initial["status"] == "RETRY_REQUIRED" and not resume_failed:
        return {"mode": "APPLY" if apply else "DRY_RUN", **initial, "processed_count": 0}
    if not apply:
        return {"mode": "DRY_RUN", **initial, "processed_count": 0}

    limit = initial["remaining_count"] if all_packages else max_packages
    processed: list[dict[str, Any]] = []
    for _ in range(limit):
        current = build_replay_plan(manifest_path, raw_root)
        if current["status"] == "COMPLETE":
            break
        if current["status"] == "BLOCKED":
            return {
                "mode": "APPLY",
                "status": "BLOCKED",
                "processed_count": len(processed),
                "processed": processed,
                "final_plan": current,
            }
        next_action = current.get("next_action")
        if not next_action:
            break
        retry = next_action["action"] == "RETRY_FULL_PACKAGE"
        if retry and not resume_failed:
            return {
                "mode": "APPLY",
                "status": "RETRY_REQUIRED",
                "processed_count": len(processed),
                "processed": processed,
                "final_plan": current,
            }

        source_path = Path(str(next_action["path"]))
        if next_action["action"] in {"REGISTER_AND_INGEST", "RETRY_FULL_PACKAGE"}:
            register_ttab_source(
                source_path,
                snapshot_at=datetime.fromisoformat(
                    str(next_action["snapshot_at"]).replace("Z", "+00:00")
                ),
                source_kind=str(next_action["source_kind"]),
            )

        try:
            result = run_ttab_once(raw_root, retry=retry)
        except Exception as exc:
            return {
                "mode": "APPLY",
                "status": "FAILED",
                "processed_count": len(processed),
                "processed": processed,
                "error": str(exc),
                "failed_source": next_action,
                "final_plan": build_replay_plan(manifest_path, raw_root),
            }
        if result.get("status") != "SUCCESS":
            return {
                "mode": "APPLY",
                "status": str(result.get("status") or "FAILED"),
                "processed_count": len(processed),
                "processed": processed,
                "result": result,
                "final_plan": build_replay_plan(manifest_path, raw_root),
            }
        processed.append(result)

    final_plan = build_replay_plan(manifest_path, raw_root)
    status = "COMPLETE" if final_plan["status"] == "COMPLETE" else "PAUSED"
    return {
        "mode": "APPLY",
        "replay_version": REPLAY_VERSION,
        "status": status,
        "processed_count": len(processed),
        "processed": processed,
        "final_plan": final_plan,
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic manifest-driven USPTO TTAB bulk corpus replay"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--max-packages", type=int, default=1)
    parser.add_argument("--resume-failed", action="store_true")
    args = parser.parse_args()
    report = execute_replay(
        args.manifest,
        get_settings().raw_data_root,
        apply=args.apply,
        all_packages=args.all,
        max_packages=args.max_packages,
        resume_failed=args.resume_failed,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 2 if report["status"] in {"BLOCKED", "FAILED", "BUSY"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
