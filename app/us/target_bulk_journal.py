from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.us.target_bulk_plan import validate_bulk_plan


BULK_JOURNAL_VERSION = "US_APPLICATION_TARGET_BULK_JOURNAL_V1"
BULK_STATES = {"PREPARED", "RUNNING", "BLOCKED", "COMPLETE"}
PACKAGE_STATES = {"PENDING", "FINAL_VERIFIED", "COMPLETE"}
_INTEGRITY_FIELD = "integrity_sha256"


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    canonical = {key: value for key, value in payload.items() if key != _INTEGRITY_FIELD}
    return (
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        + "\n"
    ).encode("utf-8")


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(payload)
    sealed[_INTEGRITY_FIELD] = hashlib.sha256(_canonical_bytes(sealed)).hexdigest()
    return sealed


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_seal(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_raw(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"US target bulk journal unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("US target bulk journal root must be an object")
    expected = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if str(payload.get(_INTEGRITY_FIELD) or "") != expected:
        raise RuntimeError("US target bulk journal integrity SHA-256 mismatch")
    return payload


def _package_key(sequence: int) -> str:
    return str(int(sequence))


def initialize_bulk_journal(
    path: Path,
    *,
    plan: dict[str, Any],
    state_dir: Path,
) -> dict[str, Any]:
    validate_bulk_plan(plan)
    if path.exists():
        raise RuntimeError("US target bulk journal already exists")
    state_dir = state_dir.resolve()
    packages: dict[str, dict[str, Any]] = {}
    for item in plan["packages"]:
        sequence = int(item["sequence"])
        token = str(item["sha256"])[:16]
        packages[_package_key(sequence)] = {
            "sequence": sequence,
            "file_name": item["file_name"],
            "sha256": item["sha256"],
            "package_id": item["package_id"],
            "status": "PENDING",
            "canary_journal_path": str(
                state_dir / f"package_{sequence:03d}_{token}.canary.json"
            ),
            "receipt_path": str(
                state_dir / f"package_{sequence:03d}_{token}.receipt.json"
            ),
            "final_row_counts": None,
            "stage_cleanup_complete": False,
        }
    payload: dict[str, Any] = {
        "journal_version": BULK_JOURNAL_VERSION,
        "revision": 1,
        "state": "PREPARED",
        "plan_sha256": plan["plan_sha256"],
        "inventory_sha256": plan["inventory_sha256"],
        "execution_main": plan["execution_main"],
        "raw_root": plan["raw_root"],
        "accepted_schema_manifest_sha256": plan["accepted_schema_manifest_sha256"],
        "start_sequence": plan["start_sequence"],
        "end_sequence": plan["end_sequence"],
        "package_count": plan["package_count"],
        "completed_package_count": 0,
        "last_completed_sequence": 0,
        "blocked": None,
        "packages": packages,
    }
    _atomic_write(path, payload)
    return _load_raw(path)


def load_bulk_journal(path: Path, *, plan: dict[str, Any]) -> dict[str, Any]:
    validate_bulk_plan(plan)
    payload = _load_raw(path)
    if payload.get("journal_version") != BULK_JOURNAL_VERSION:
        raise RuntimeError("unsupported US target bulk journal version")
    if payload.get("state") not in BULK_STATES:
        raise RuntimeError("US target bulk journal state is invalid")
    bindings = {
        "plan_sha256": plan["plan_sha256"],
        "inventory_sha256": plan["inventory_sha256"],
        "execution_main": plan["execution_main"],
        "raw_root": plan["raw_root"],
        "accepted_schema_manifest_sha256": plan["accepted_schema_manifest_sha256"],
        "start_sequence": plan["start_sequence"],
        "end_sequence": plan["end_sequence"],
        "package_count": plan["package_count"],
    }
    for field, expected in bindings.items():
        if payload.get(field) != expected:
            raise RuntimeError(f"US target bulk journal binding drifted: {field}")
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        raise RuntimeError("US target bulk journal packages are missing")
    expected_keys = {_package_key(int(item["sequence"])) for item in plan["packages"]}
    if set(packages) != expected_keys:
        raise RuntimeError("US target bulk journal package set drifted")
    for item in plan["packages"]:
        key = _package_key(int(item["sequence"]))
        state = packages[key]
        if not isinstance(state, dict):
            raise RuntimeError(f"US target bulk package journal state malformed: {key}")
        if state.get("status") not in PACKAGE_STATES:
            raise RuntimeError(f"US target bulk package state invalid: {key}")
        for field in ("sequence", "file_name", "sha256", "package_id"):
            if state.get(field) != item[field]:
                raise RuntimeError(
                    f"US target bulk package identity drifted: sequence={key} field={field}"
                )
    return payload


def _persist_revision(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    current = _load_raw(path)
    expected_revision = int(payload["revision"])
    if int(current.get("revision") or 0) != expected_revision:
        raise RuntimeError("US target bulk journal revision changed concurrently")
    next_payload = dict(payload)
    next_payload.pop(_INTEGRITY_FIELD, None)
    next_payload["revision"] = expected_revision + 1
    _atomic_write(path, next_payload)
    return _load_raw(path)


def mark_bulk_running(path: Path, *, plan: dict[str, Any]) -> dict[str, Any]:
    payload = load_bulk_journal(path, plan=plan)
    if payload["state"] == "COMPLETE":
        return payload
    if payload["state"] not in {"PREPARED", "RUNNING", "BLOCKED"}:
        raise RuntimeError("US target bulk journal cannot enter RUNNING")
    payload["state"] = "RUNNING"
    payload["blocked"] = None
    return _persist_revision(path, payload)


def mark_package_final_verified(
    path: Path,
    *,
    plan: dict[str, Any],
    sequence: int,
    final_row_counts: dict[str, int],
) -> dict[str, Any]:
    payload = load_bulk_journal(path, plan=plan)
    package = payload["packages"][_package_key(sequence)]
    if package["status"] == "COMPLETE":
        return payload
    if package["status"] not in {"PENDING", "FINAL_VERIFIED"}:
        raise RuntimeError("US target bulk package cannot enter FINAL_VERIFIED")
    package["status"] = "FINAL_VERIFIED"
    package["final_row_counts"] = {
        str(table): int(count) for table, count in sorted(final_row_counts.items())
    }
    package["stage_cleanup_complete"] = False
    payload["state"] = "RUNNING"
    payload["blocked"] = None
    return _persist_revision(path, payload)


def mark_package_complete(
    path: Path,
    *,
    plan: dict[str, Any],
    sequence: int,
) -> dict[str, Any]:
    payload = load_bulk_journal(path, plan=plan)
    package = payload["packages"][_package_key(sequence)]
    if package["status"] == "COMPLETE":
        return payload
    if package["status"] != "FINAL_VERIFIED":
        raise RuntimeError("US target bulk package cleanup requires FINAL_VERIFIED")
    if not isinstance(package.get("final_row_counts"), dict):
        raise RuntimeError("US target bulk package final row counts are missing")
    package["status"] = "COMPLETE"
    package["stage_cleanup_complete"] = True

    ordered_sequences = [int(item["sequence"]) for item in plan["packages"]]
    completed_count = 0
    last_completed = 0
    for candidate in ordered_sequences:
        if payload["packages"][_package_key(candidate)]["status"] != "COMPLETE":
            break
        completed_count += 1
        last_completed = candidate
    payload["completed_package_count"] = completed_count
    payload["last_completed_sequence"] = last_completed
    if completed_count == len(ordered_sequences):
        payload["state"] = "COMPLETE"
    else:
        payload["state"] = "RUNNING"
    payload["blocked"] = None
    return _persist_revision(path, payload)


def mark_bulk_blocked(
    path: Path,
    *,
    plan: dict[str, Any],
    sequence: int,
    error: BaseException,
) -> dict[str, Any]:
    payload = load_bulk_journal(path, plan=plan)
    if payload["state"] == "COMPLETE":
        raise RuntimeError("completed US target bulk journal cannot be blocked")
    payload["state"] = "BLOCKED"
    payload["blocked"] = {
        "sequence": int(sequence),
        "error_type": type(error).__name__,
        "message": str(error),
        "automatic_next_package": False,
    }
    return _persist_revision(path, payload)
