from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from app.us.target_bulk_plan import (
    FIRST_BULK_SEQUENCE,
    _canonical_sha256,
    validate_bulk_plan,
)
from app.us.target_canary import write_receipt


BATCH_MANIFEST_VERSION = "US_APPLICATION_TARGET_BULK_BATCH_MANIFEST_V1"


def _manifest_sha256(payload: dict[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    canonical = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _child_plan(master: dict[str, Any], sequence: int) -> dict[str, Any]:
    validate_bulk_plan(master)
    if sequence < int(master["start_sequence"]) or sequence > int(master["end_sequence"]):
        raise ValueError("child sequence is outside the frozen master plan")
    bridge = deepcopy(master["packages"][0])
    suffix = [
        deepcopy(item)
        for item in master["packages"][1:]
        if int(item.get("sequence") or 0) == sequence
    ]
    if len(suffix) != 1:
        raise RuntimeError(f"master plan does not contain exactly one suffix package: {sequence}")

    excluded = {
        "plan_sha256",
        "required_authority_token",
        "start_sequence",
        "end_sequence",
        "suffix_package_count",
        "package_count",
        "packages",
    }
    contract = {key: deepcopy(value) for key, value in master.items() if key not in excluded}
    contract.update(
        {
            "start_sequence": sequence,
            "end_sequence": sequence,
            "suffix_package_count": 1,
            "package_count": 2,
            "packages": [bridge, suffix[0]],
        }
    )
    plan_sha = _canonical_sha256(contract)
    child = {
        **contract,
        "plan_sha256": plan_sha,
        "required_authority_token": f"GO #545 bounded US Application bulk replay {plan_sha}",
    }
    validate_bulk_plan(child)
    return child


def derive_batch_manifest(
    master_plan: dict[str, Any],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Freeze every child execution plan under one already-frozen master plan."""
    validate_bulk_plan(master_plan)
    start = int(master_plan["start_sequence"])
    end = int(master_plan["end_sequence"])
    if start < FIRST_BULK_SEQUENCE or end < start:
        raise RuntimeError("master plan bounded suffix is invalid")

    children: list[dict[str, Any]] = []
    resolved_dir = output_dir.resolve() if output_dir is not None else None
    if resolved_dir is not None:
        resolved_dir.mkdir(parents=True, exist_ok=True)

    for sequence in range(start, end + 1):
        child = _child_plan(master_plan, sequence)
        plan_path: str | None = None
        if resolved_dir is not None:
            path = resolved_dir / f"child_{sequence:03d}_{child['plan_sha256']}.json"
            write_receipt(path, child)
            plan_path = str(path)
        children.append(
            {
                "sequence": sequence,
                "plan_sha256": child["plan_sha256"],
                "required_authority_token": child["required_authority_token"],
                "plan_path": plan_path,
            }
        )

    payload: dict[str, Any] = {
        "manifest_version": BATCH_MANIFEST_VERSION,
        "read_only": True,
        "production_mutation_authorized": False,
        "master_plan_sha256": master_plan["plan_sha256"],
        "inventory_sha256": master_plan["inventory_sha256"],
        "execution_main": master_plan["execution_main"],
        "bridge_sequence": 1,
        "accepted_existing_target_sequence": 2,
        "start_sequence": start,
        "end_sequence": end,
        "child_count": len(children),
        "children": children,
    }
    payload["manifest_sha256"] = _manifest_sha256(payload)
    return payload


def validate_batch_manifest(
    manifest: dict[str, Any],
    *,
    master_plan: dict[str, Any],
) -> None:
    validate_bulk_plan(master_plan)
    if manifest.get("manifest_version") != BATCH_MANIFEST_VERSION:
        raise RuntimeError("unsupported US target bulk batch manifest version")
    if not bool(manifest.get("read_only")) or bool(
        manifest.get("production_mutation_authorized")
    ):
        raise RuntimeError("US target bulk batch manifest must be read-only before approval")
    if manifest.get("master_plan_sha256") != master_plan["plan_sha256"]:
        raise RuntimeError("US target bulk batch manifest master plan binding drifted")
    if manifest.get("inventory_sha256") != master_plan["inventory_sha256"]:
        raise RuntimeError("US target bulk batch manifest inventory binding drifted")
    if manifest.get("execution_main") != master_plan["execution_main"]:
        raise RuntimeError("US target bulk batch manifest execution main drifted")
    if manifest.get("manifest_sha256") != _manifest_sha256(manifest):
        raise RuntimeError("US target bulk batch manifest integrity SHA-256 mismatch")

    children = manifest.get("children")
    if not isinstance(children, list):
        raise RuntimeError("US target bulk batch manifest children are missing")
    start = int(master_plan["start_sequence"])
    end = int(master_plan["end_sequence"])
    if [int(item.get("sequence") or 0) for item in children] != list(range(start, end + 1)):
        raise RuntimeError("US target bulk batch manifest child sequence coverage drifted")
    if int(manifest.get("child_count") or 0) != len(children):
        raise RuntimeError("US target bulk batch manifest child count drifted")

    for item in children:
        sequence = int(item["sequence"])
        expected = _child_plan(master_plan, sequence)
        if item.get("plan_sha256") != expected["plan_sha256"]:
            raise RuntimeError(f"US target bulk child plan SHA drifted: {sequence}")
        if item.get("required_authority_token") != expected["required_authority_token"]:
            raise RuntimeError(f"US target bulk child authority binding drifted: {sequence}")


def write_batch_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_receipt(path, manifest)
