from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.scanner import CHUNK_SIZE, sha256_file
from app.us.package_meta import infer_us_package_descriptor
from app.us.source_preflight import ARCHIVE_DIGEST_SUFFIX_RE, build_preflight


STAGING_VERSION = "US_SOURCE_STAGING_V1"


def _canonical_source_name(path: Path) -> str:
    if infer_us_package_descriptor(path).package_kind != "UNKNOWN":
        return path.name
    match = ARCHIVE_DIGEST_SUFFIX_RE.match(path.stem)
    if not match:
        raise ValueError(f"Cannot recover canonical USPTO package name: {path.name}")
    candidate = match.group("base") + path.suffix
    if infer_us_package_descriptor(candidate).package_kind == "UNKNOWN":
        raise ValueError(f"Cannot recover canonical USPTO package name: {path.name}")
    return candidate


def _candidate_rows(preflight: dict[str, Any], raw_root: Path) -> list[dict[str, Any]]:
    incoming = raw_root / "incoming" / "us"
    rows: list[dict[str, Any]] = []
    for source in preflight.get("replay_plan") or []:
        if not source.get("needs_staging_from_archive"):
            continue
        source_path = Path(str(source["path"]))
        canonical_name = _canonical_source_name(source_path)
        destination = incoming / canonical_name
        expected_sha = str(source.get("sha256") or "").lower()
        action = "COPY_REQUIRED"
        destination_sha = None
        if destination.is_file():
            destination_sha = sha256_file(destination).lower()
            action = "ALREADY_STAGED" if destination_sha == expected_sha else "CONFLICT"
        elif destination.exists():
            action = "CONFLICT"
        rows.append(
            {
                "sequence": int(source["sequence"]),
                "package_kind": source["package_kind"],
                "partition_value": source["partition_value"],
                "source_path": str(source_path),
                "source_file_name": source["file_name"],
                "canonical_file_name": canonical_name,
                "destination_path": str(destination),
                "expected_sha256": expected_sha,
                "destination_sha256": destination_sha,
                "action": action,
            }
        )
    return rows


def build_staging_plan(
    raw_root: Path,
    *,
    expected_history_parts: int | None,
    deep_source_test: bool = False,
) -> dict[str, Any]:
    preflight = build_preflight(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
    )
    rows: list[dict[str, Any]] = []
    planning_error = ""
    if preflight.get("safe_to_replay"):
        try:
            rows = _candidate_rows(preflight, raw_root)
        except ValueError as exc:
            planning_error = str(exc)

    conflicts = [row for row in rows if row["action"] == "CONFLICT"]
    copy_required = [row for row in rows if row["action"] == "COPY_REQUIRED"]
    already_staged = [row for row in rows if row["action"] == "ALREADY_STAGED"]

    if not preflight.get("safe_to_replay"):
        status = "BLOCKED"
        blocked_reason = "source_preflight_not_safe"
    elif planning_error:
        status = "BLOCKED"
        blocked_reason = "canonical_name_recovery_failed"
    elif conflicts:
        status = "BLOCKED"
        blocked_reason = "staging_destination_conflict"
    elif copy_required:
        status = "READY"
        blocked_reason = ""
    else:
        status = "NOOP"
        blocked_reason = ""

    return {
        "status": status,
        "staging_version": STAGING_VERSION,
        "apply_required": status == "READY",
        "blocked_reason": blocked_reason,
        "planning_error": planning_error,
        "preflight": preflight,
        "copy_required_count": len(copy_required),
        "already_staged_count": len(already_staged),
        "conflict_count": len(conflicts),
        "staging_rows": rows,
        "policy_note": (
            "Staging copies only authoritative archive-selected sources into incoming using their "
            "canonical modeled USPTO filename. It never overwrites an existing destination and "
            "does not touch PostgreSQL or ClickHouse."
        ),
    }


def _exclusive_copy_verified(source: Path, destination: Path, expected_sha: str) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            while chunk := input_stream.read(CHUNK_SIZE):
                output_stream.write(chunk)
                digest.update(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
    except Exception:
        # A partially written canonical file must never masquerade as a successful stage.
        try:
            if destination.exists():
                destination.unlink()
        except OSError:
            pass
        raise

    actual = digest.hexdigest().lower()
    if actual != expected_sha.lower():
        try:
            destination.unlink()
        finally:
            raise RuntimeError(
                f"Staged source SHA-256 mismatch for {destination.name}: "
                f"expected={expected_sha.lower()} actual={actual}"
            )
    return actual


def _preverify_mutation_inputs(rows: list[dict[str, Any]]) -> None:
    """Fail before the first copy if any planned source/destination is no longer safe."""
    for row in rows:
        if row["action"] != "COPY_REQUIRED":
            continue
        source = Path(row["source_path"])
        destination = Path(row["destination_path"])
        expected_sha = str(row["expected_sha256"]).lower()
        if not source.is_file():
            raise RuntimeError(f"Archive source disappeared after preflight: {source}")
        source_sha = sha256_file(source).lower()
        if source_sha != expected_sha:
            raise RuntimeError(
                f"Archive source changed after preflight: {source.name}: "
                f"expected={expected_sha} actual={source_sha}"
            )
        if destination.exists():
            if destination.is_file() and sha256_file(destination).lower() == expected_sha:
                continue
            raise RuntimeError(
                f"Refusing to overwrite existing staging destination: {destination}"
            )


def apply_staging(
    raw_root: Path,
    *,
    expected_history_parts: int | None,
    deep_source_test: bool = False,
) -> dict[str, Any]:
    plan = build_staging_plan(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
    )
    if plan["status"] == "BLOCKED":
        raise RuntimeError(
            f"US source staging blocked: {plan['blocked_reason'] or plan['planning_error']}"
        )
    if plan["status"] == "NOOP":
        return {
            **plan,
            "status": "NOOP",
            "applied": False,
            "copied": [],
            "postflight": plan["preflight"],
        }

    # Verify the whole mutation set before copying the first byte. This avoids a later
    # stale archive source causing a partially staged multi-file plan.
    _preverify_mutation_inputs(plan["staging_rows"])

    copied: list[dict[str, Any]] = []
    for row in plan["staging_rows"]:
        if row["action"] != "COPY_REQUIRED":
            continue
        source = Path(row["source_path"])
        destination = Path(row["destination_path"])
        expected_sha = str(row["expected_sha256"]).lower()

        # A destination can still appear between preverification and this copy. Never overwrite it.
        if destination.exists():
            if destination.is_file() and sha256_file(destination).lower() == expected_sha:
                copied.append(
                    {
                        **row,
                        "result": "ALREADY_STAGED_AFTER_PLAN",
                        "actual_sha256": expected_sha,
                    }
                )
                continue
            raise RuntimeError(
                f"Refusing to overwrite existing staging destination: {destination}"
            )

        actual_sha = _exclusive_copy_verified(source, destination, expected_sha)
        copied.append({**row, "result": "COPIED", "actual_sha256": actual_sha})

    postflight = build_preflight(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
    )
    if not postflight.get("safe_to_replay"):
        raise RuntimeError(
            "US source staging completed copies but postflight is not safe to replay: "
            f"status={postflight.get('status')} issues={postflight.get('hard_issue_types')} "
            f"not_ready={postflight.get('not_ready_reasons')}"
        )

    remaining_archive = sum(
        1
        for row in postflight.get("replay_plan") or []
        if row.get("needs_staging_from_archive")
    )
    if remaining_archive:
        raise RuntimeError(
            f"US source staging postflight still requires {remaining_archive} archive source(s)"
        )

    return {
        **plan,
        "status": "APPLIED",
        "applied": True,
        "copied": copied,
        "copied_count": sum(1 for row in copied if row["result"] == "COPIED"),
        "postflight": postflight,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Guarded USPTO archive-to-incoming source staging"
    )
    parser.add_argument("--raw-root", type=Path, default=None)
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--deep-source-test", action="store_true")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform verified copies. Without this flag the command is dry-run only.",
    )
    args = parser.parse_args()
    if args.expected_history_parts < 1:
        parser.error("--expected-history-parts must be at least 1")
    raw_root = args.raw_root
    if raw_root is None:
        from app.config import get_settings

        raw_root = get_settings().raw_data_root

    report = (
        apply_staging(
            raw_root,
            expected_history_parts=args.expected_history_parts,
            deep_source_test=args.deep_source_test,
        )
        if args.apply
        else build_staging_plan(
            raw_root,
            expected_history_parts=args.expected_history_parts,
            deep_source_test=args.deep_source_test,
        )
    )
    report["mode"] = "APPLY" if args.apply else "DRY_RUN"
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
