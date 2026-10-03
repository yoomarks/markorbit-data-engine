"""Freeze and validate an exact-authority SG corpus refresh plan.

Freeze and validation are read-only. The existing owner DAG remains the only
component that downloads or promotes a corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from .ipos_sg_acceptance import probe_ipos_live_source
from .ipos_sg_metadata_probe import probe_ipos_metadata
from .ipos_sg_snapshot_archive import verify_archive_point
from .ipos_sg_state import audit_ipos_state

PLAN_KIND = "IPOS_SG_ONE_SHOT_REFRESH_PLAN_V1"
AUTHORITY_PREFIX = "GO #849 SG-CORPUS-REFRESH"
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def require(ok: bool, why: str) -> None:
    if not ok:
        raise RuntimeError(why)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    require(path.is_file() and path.stat().st_size <= 2 * 1024 * 1024, f"{label} missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"{label} is invalid JSON") from exc
    require(isinstance(payload, dict), f"{label} must be a JSON object")
    return payload


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    require(result.returncode == 0, "git state check failed: " + result.stderr[-300:])
    return result.stdout.strip()


def require_clean_exact_main(repo: Path, expected: str) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", expected) is not None, "invalid expected main SHA")
    require(_git(repo, "rev-parse", "HEAD") == expected, "working HEAD differs from plan main")
    require(
        _git(repo, "rev-parse", "origin/main") == expected,
        "origin/main differs from plan main",
    )
    require(not _git(repo, "status", "--porcelain=v1"), "refresh worktree must be clean")


def accepted_state(state_dir: Path) -> dict[str, Any]:
    audit = audit_ipos_state(state_dir)
    require(
        audit.status == "READY"
        and audit.safe_to_run
        and audit.retained_full_snapshot_count == 1
        and audit.orphan_full_snapshot_count == 0
        and not audit.transient_part_paths,
        "accepted SG state is not READY and singular",
    )
    current_path = state_dir / "current.json"
    current = _read_json(current_path, "accepted current pointer")
    content_hash = str(current.get("content_hash") or "")
    require(SHA_PATTERN.fullmatch(content_hash) is not None, "accepted current hash is invalid")
    expected_reference = f"snapshots/{content_hash}.csv"
    require(
        current.get("storage_reference") == expected_reference,
        "accepted current storage reference drift",
    )
    manifest_path = state_dir / "snapshots" / f"{content_hash}.manifest.json"
    manifest = _read_json(manifest_path, "accepted current manifest")
    snapshot_path = state_dir / expected_reference
    require(
        manifest.get("content_hash") == content_hash
        and int(manifest.get("row_count") or 0) > 0
        and SHA_PATTERN.fullmatch(str(manifest.get("schema_hash") or "")) is not None
        and snapshot_path.is_file()
        and not snapshot_path.is_symlink(),
        "accepted current manifest or snapshot drift",
    )
    require(sha256_file(snapshot_path) == content_hash, "accepted current snapshot hash drift")
    operator_path = state_dir / "acceptance" / "operator_latest.json"
    operator = _read_json(operator_path, "accepted SG operator receipt")
    full = operator.get("full_corpus")
    require(
        operator.get("status") == "PASS"
        and isinstance(full, dict)
        and full.get("content_hash") == content_hash
        and int(full.get("row_count") or 0) == int(manifest["row_count"]),
        "accepted operator receipt differs from current",
    )
    return {
        "state_dir": str(state_dir.resolve()),
        "current_content_hash": content_hash,
        "current_pointer_sha256": sha256_file(current_path),
        "manifest_sha256": sha256_file(manifest_path),
        "row_count": int(manifest["row_count"]),
        "snapshot_bytes": snapshot_path.stat().st_size,
        "schema_hash": manifest["schema_hash"],
        "operator_receipt_sha256": sha256_file(operator_path),
        "operator_completed_at": operator.get("completed_at"),
        "audit_status": audit.status,
        "retained_full_snapshot_count": audit.retained_full_snapshot_count,
        "orphan_full_snapshot_count": audit.orphan_full_snapshot_count,
    }


def source_observation() -> dict[str, Any]:
    metadata = probe_ipos_metadata()
    live = probe_ipos_live_source(resolve_download_url=False)
    require(
        metadata.dataset_id == live.dataset_id == IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id
        and metadata.source_revision_trusted is False
        and live.download_url_resolved is False,
        "SG public source probe unexpectedly grants corpus identity or download",
    )
    fields = list(live.field_names)
    fields_sha = hashlib.sha256(
        json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "dataset_id": metadata.dataset_id,
        "metadata_checked_at": metadata.checked_at.isoformat(),
        "source_updated_at": metadata.source_updated_at.isoformat(),
        "dataset_size_bytes": metadata.dataset_size_bytes,
        "column_metadata_sha256": metadata.column_metadata_sha256,
        "metadata_identity_sha256": metadata.metadata_identity_sha256,
        "source_revision_trusted": False,
        "live_checked_at": live.checked_at.isoformat(),
        "live_total_rows": live.total_rows,
        "live_field_names_sha256": fields_sha,
        "download_url_resolved": False,
    }


def archive_evidence(
    point_path: Path,
    *,
    tier: str,
    current_hash: str,
    current_bytes: int,
    restore_receipt_path: Path | None = None,
) -> dict[str, Any]:
    point = _read_json(point_path, f"{tier} archive point")
    slot = str(point.get("slot") or "")
    revision = None
    if point_path.stem != slot:
        require(
            point_path.stem == f"{slot}--{current_hash}",
            f"{tier} archive point filename drift",
        )
        revision = current_hash
    root = point_path.parents[2]
    verified = verify_archive_point(root, tier, slot, revision_sha256=revision)
    require(
        point.get("tier") == tier
        and point.get("content_hash") == current_hash
        and point.get("object_reference") == f"objects/{current_hash}.csv",
        f"{tier} archive point does not preserve accepted current",
    )
    require(verified == point, f"{tier} archive point verification drift")
    obj = root / point["object_reference"]
    require(
        obj.is_file() and not obj.is_symlink() and obj.stat().st_size == current_bytes,
        f"{tier} archive object is absent or has wrong size",
    )
    result = {
        "tier": tier,
        "point_path": str(point_path.resolve()),
        "point_sha256": sha256_file(point_path),
        "slot": point.get("slot"),
        "content_hash": current_hash,
        "object_path": str(obj.resolve()),
        "object_bytes": obj.stat().st_size,
    }
    if restore_receipt_path is not None:
        receipt = _read_json(restore_receipt_path, "monthly restore receipt")
        require(
            receipt.get("status") == "PASS"
            and receipt.get("content_sha256") == current_hash
            and int(receipt.get("restored_bytes") or 0) == current_bytes
            and receipt.get("restored_sha256_verified_by_archive_runtime") is True
            and receipt.get("scratch_cleaned") is True
            and receipt.get("backup_device_distinct_from_restore") is True,
            "monthly restore drill does not accept current snapshot",
        )
        result["restore_receipt_path"] = str(restore_receipt_path.resolve())
        result["restore_receipt_sha256"] = sha256_file(restore_receipt_path)
    return result


def build_plan(
    *,
    execution_main_sha: str,
    state: dict[str, Any],
    source: dict[str, Any],
    weekly: dict[str, Any],
    monthly: dict[str, Any],
) -> dict[str, Any]:
    require(source["dataset_id"] == IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id, "source drift")
    require(source["live_total_rows"] >= state["row_count"], "live SG row count regressed")
    require(
        weekly["content_hash"] == monthly["content_hash"] == state["current_content_hash"],
        "archive drift",
    )
    return {
        "kind": PLAN_KIND,
        "issue": 849,
        "status": "FROZEN_NO_APPLY",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "execution_main_sha": execution_main_sha,
        "accepted_current": state,
        "source_observation": source,
        "source_row_delta_from_current": source["live_total_rows"] - state["row_count"],
        "pre_refresh_archives": {"weekly": weekly, "monthly": monthly},
        "operator_dag": "IPOS_SG_OPERATOR_DAG_V1",
        "one_shot_refresh_only": True,
        "cn_serving_pre_post_regression_required": True,
        "accepted_current_promotion_requires_full_operator_pass": True,
        "recurring_schedule_enabled": False,
        "additional_refresh_authorized": False,
        "source_cleanup_authorized": False,
        "archive_cleanup_authorized": False,
        "production_mutation_performed": False,
    }


def authority(plan_sha: str) -> str:
    return f"{AUTHORITY_PREFIX} {plan_sha} ONE-SHOT-NO-SCHEDULE"


def validate_apply_authority(plan: dict[str, Any], plan_sha: str, token: str) -> None:
    require(
        SHA_PATTERN.fullmatch(plan_sha) is not None
        and plan.get("kind") == PLAN_KIND
        and plan.get("issue") == 849
        and plan.get("status") == "FROZEN_NO_APPLY"
        and plan.get("operator_dag") == "IPOS_SG_OPERATOR_DAG_V1"
        and plan.get("one_shot_refresh_only") is True
        and plan.get("cn_serving_pre_post_regression_required") is True
        and plan.get("recurring_schedule_enabled") is False
        and plan.get("additional_refresh_authorized") is False
        and plan.get("source_cleanup_authorized") is False
        and plan.get("archive_cleanup_authorized") is False
        and plan.get("production_mutation_performed") is False,
        "frozen SG refresh plan contract drift",
    )
    require(token == authority(plan_sha), "exact SG one-shot refresh authority required")


def _compare_live(plan: dict[str, Any], state_dir: Path) -> None:
    actual_state = accepted_state(state_dir)
    expected_state = plan["accepted_current"]
    require(actual_state == expected_state, "accepted SG current changed; refreeze plan")
    actual_source = source_observation()
    expected_source = plan["source_observation"]
    stable_keys = (
        "dataset_id",
        "source_updated_at",
        "dataset_size_bytes",
        "column_metadata_sha256",
        "metadata_identity_sha256",
        "source_revision_trusted",
        "live_total_rows",
        "live_field_names_sha256",
        "download_url_resolved",
    )
    require(
        all(actual_source[key] == expected_source[key] for key in stable_keys),
        "official SG source changed; refreeze plan",
    )
    for tier in ("weekly", "monthly"):
        expected = plan["pre_refresh_archives"][tier]
        actual = archive_evidence(
            Path(expected["point_path"]),
            tier=tier,
            current_hash=actual_state["current_content_hash"],
            current_bytes=actual_state["snapshot_bytes"],
            restore_receipt_path=(
                Path(expected["restore_receipt_path"]) if tier == "monthly" else None
            ),
        )
        require(actual == expected, f"{tier} archive evidence changed; refreeze plan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze-plan", type=Path)
    mode.add_argument("--validate-apply", action="store_true")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--expected-main-sha", default="")
    parser.add_argument("--weekly-point", type=Path)
    parser.add_argument("--monthly-point", type=Path)
    parser.add_argument("--monthly-restore-receipt", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha", default="")
    parser.add_argument("--authority-token", default="")
    args = parser.parse_args()
    if args.freeze_plan is not None:
        require(
            args.expected_main_sha
            and args.weekly_point
            and args.monthly_point
            and args.monthly_restore_receipt
            and not args.plan
            and not args.authority_token,
            "freeze requires main, weekly, monthly and restore evidence only",
        )
        require(not args.freeze_plan.exists(), "SG refresh plan already exists: no overwrite")
        require_clean_exact_main(args.repo, args.expected_main_sha)
        state = accepted_state(args.state_dir)
        source = source_observation()
        weekly = archive_evidence(
            args.weekly_point,
            tier="weekly",
            current_hash=state["current_content_hash"],
            current_bytes=state["snapshot_bytes"],
        )
        monthly = archive_evidence(
            args.monthly_point,
            tier="monthly",
            current_hash=state["current_content_hash"],
            current_bytes=state["snapshot_bytes"],
            restore_receipt_path=args.monthly_restore_receipt,
        )
        plan = build_plan(
            execution_main_sha=args.expected_main_sha,
            state=state,
            source=source,
            weekly=weekly,
            monthly=monthly,
        )
        args.freeze_plan.parent.mkdir(parents=True, exist_ok=True)
        with args.freeze_plan.open("x", encoding="utf-8", newline="\n") as target:
            json.dump(plan, target, ensure_ascii=False, sort_keys=True, indent=2)
            target.write("\n")
        plan_sha = sha256_file(args.freeze_plan)
        print("IPOS_SG_REFRESH_PLAN_SHA256=" + plan_sha, flush=True)
        print("IPOS_SG_REFRESH_PLAN_STATUS=FROZEN_NO_APPLY", flush=True)
        print("IPOS_SG_REFRESH_EXACT_AUTHORITY=" + authority(plan_sha), flush=True)
        return
    require(
        args.plan is not None and SHA_PATTERN.fullmatch(args.plan_sha) is not None,
        "exact frozen SG refresh plan and SHA required",
    )
    require(sha256_file(args.plan) == args.plan_sha, "SG refresh plan SHA mismatch")
    plan = _read_json(args.plan, "frozen SG refresh plan")
    validate_apply_authority(plan, args.plan_sha, args.authority_token)
    require_clean_exact_main(args.repo, plan["execution_main_sha"])
    _compare_live(plan, args.state_dir)
    print("IPOS_SG_REFRESH_AUTHORITY_VALIDATED", flush=True)


if __name__ == "__main__":
    main()
