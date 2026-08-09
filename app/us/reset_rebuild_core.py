from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.us.audit_real_data import ALL_TABLE_KEYS
from app.us.migrations import US_SCHEMA_VERSION, ensure_us_m1_schema
from app.us.package_meta import infer_us_package_descriptor
from app.us.repository import list_us_replay_registry
from app.us.run_guard import us_ingestion_guard
from app.us.source_preflight import build_preflight


RESET_VERSION = "US_CLEAN_REBUILD_RESET_V1"
RESET_CONFIRMATION = "RESET-US-M1.3"
MANIFEST_DIRECTORY = Path("rebuild_manifests") / "us"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _table_counts() -> dict[str, int]:
    client = clickhouse_client()
    counts: dict[str, int] = {}
    for table in ALL_TABLE_KEYS:
        rows = client.query(f"SELECT count() FROM markorbit_facts.{table}").result_rows
        counts[table] = int(rows[0][0] if rows else 0)
    return counts


def _registry_index(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        digest = str(row.get("sha256") or "").lower()
        grouped.setdefault(digest, []).append(row)
    duplicates = sorted(
        digest for digest, items in grouped.items() if digest and len(items) > 1
    )
    return {digest: items[0] for digest, items in grouped.items() if digest}, duplicates


def build_reset_plan(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
    registry_rows: list[dict[str, Any]] | None = None,
    table_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    preflight = build_preflight(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
    )
    registry = list_us_replay_registry() if registry_rows is None else list(registry_rows)
    counts = _table_counts() if table_counts is None else dict(table_counts)
    registry_by_sha, duplicate_registry_shas = _registry_index(registry)
    source_plan = list(preflight.get("replay_plan") or [])
    source_by_sha = {
        str(row.get("sha256") or "").lower(): row
        for row in source_plan
        if row.get("sha256")
    }

    blockers: list[str] = []
    details: dict[str, Any] = {}
    if not preflight.get("safe_to_replay"):
        blockers.append("source_preflight_not_safe")
    if int(preflight.get("archive_staging_required_count") or 0):
        blockers.append("archive_sources_must_be_staged_before_reset")
    if duplicate_registry_shas:
        blockers.append("duplicate_registry_sha256")
        details["duplicate_registry_sha256"] = duplicate_registry_shas

    extra_registry = [
        {
            "package_id": str(row.get("package_id") or ""),
            "file_name": str(row.get("file_name") or ""),
            "sha256": str(row.get("sha256") or "").lower(),
            "status": str(row.get("status") or ""),
        }
        for row in registry
        if str(row.get("sha256") or "").lower() not in source_by_sha
    ]
    if extra_registry:
        blockers.append("registered_us_package_not_in_source_plan")
        details["extra_registry_packages"] = extra_registry

    identity_mismatches: list[dict[str, Any]] = []
    for digest, registry_row in registry_by_sha.items():
        source = source_by_sha.get(digest)
        if source is None:
            continue
        if (
            str(registry_row.get("package_kind") or "") != str(source["package_kind"])
            or str(registry_row.get("partition_value") or "")
            != str(source["partition_value"])
        ):
            identity_mismatches.append(
                {
                    "sha256": digest,
                    "package_id": str(registry_row.get("package_id") or ""),
                    "source_package_kind": source["package_kind"],
                    "source_partition_value": source["partition_value"],
                    "registry_package_kind": registry_row.get("package_kind"),
                    "registry_partition_value": registry_row.get("partition_value"),
                }
            )
    if identity_mismatches:
        blockers.append("registry_source_identity_mismatch")
        details["registry_source_identity_mismatches"] = identity_mismatches

    reset_rows: list[dict[str, Any]] = []
    for source in source_plan:
        digest = str(source["sha256"]).lower()
        registry_row = registry_by_sha.get(digest)
        if registry_row is None:
            continue
        path = Path(str(source["path"]))
        descriptor = infer_us_package_descriptor(path)
        if descriptor.package_kind == "UNKNOWN":
            blockers.append("source_descriptor_became_unknown")
            details.setdefault("unknown_descriptors", []).append(str(path))
            continue
        package_sequence = int(registry_row.get("package_sequence") or 0)
        file_size = (
            int(path.stat().st_size)
            if path.is_file()
            else int(registry_row.get("file_size") or 0)
        )
        reset_rows.append(
            {
                "sequence": int(source["sequence"]),
                "package_id": str(registry_row["package_id"]),
                "package_sequence": package_sequence,
                "sha256": digest,
                "file_name": path.name,
                "file_path": str(path),
                "file_size": file_size,
                "package_kind": descriptor.package_kind,
                "partition_dimension": descriptor.partition_dimension,
                "partition_value": descriptor.partition_value,
                "source_period_start": descriptor.source_period_start,
                "source_period_end": descriptor.source_period_end,
                "source_sequence": descriptor.source_sequence,
                "source_rank": descriptor.source_rank(package_sequence),
                "previous_status": str(registry_row.get("status") or ""),
                "previous_source_rank": int(registry_row.get("source_rank") or 0),
            }
        )

    blockers = list(dict.fromkeys(blockers))
    has_fact_rows = any(int(value or 0) for value in counts.values())
    has_registry_rows = bool(registry)
    if blockers:
        status = "BLOCKED"
    elif has_fact_rows or has_registry_rows:
        status = "READY"
    else:
        status = "NOOP"

    manifest_basis = {
        "reset_version": RESET_VERSION,
        "expected_history_parts": expected_history_parts,
        "preflight": preflight,
        "registry": registry,
        "table_counts": counts,
        "reset_rows": reset_rows,
    }
    return {
        "status": status,
        "reset_version": RESET_VERSION,
        "apply_required": status == "READY",
        "safe_to_reset": status in {"READY", "NOOP"},
        "expected_history_parts": expected_history_parts,
        "preflight": preflight,
        "registered_package_count": len(registry),
        "registered_plan_package_count": len(reset_rows),
        "unregistered_source_count": len(source_plan) - len(reset_rows),
        "table_counts": counts,
        "total_fact_rows": sum(int(value or 0) for value in counts.values()),
        "reset_rows": reset_rows,
        "blockers": blockers,
        "blocker_details": details,
        "manifest_fingerprint": _fingerprint(manifest_basis),
        "policy_note": (
            "Clean rebuild reset never deletes US package identities. It truncates only the eleven "
            "US fact tables, then resets source-plan registry rows to REGISTERED with empty profiles. "
            "Unregistered source-plan packages remain unregistered and will be registered by the "
            "deterministic replay executor. CN tables and non-US registry rows are out of scope."
        ),
    }


def _manifest_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reset_version": RESET_VERSION,
        "manifest_fingerprint": plan["manifest_fingerprint"],
        "expected_history_parts": plan["expected_history_parts"],
        "preflight_status": plan["preflight"].get("status"),
        "source_plan": plan["preflight"].get("replay_plan") or [],
        "registered_package_count": plan["registered_package_count"],
        "registered_plan_package_count": plan["registered_plan_package_count"],
        "unregistered_source_count": plan["unregistered_source_count"],
        "table_counts": plan["table_counts"],
        "total_fact_rows": plan["total_fact_rows"],
        "reset_rows": plan["reset_rows"],
        "blockers": plan["blockers"],
        "blocker_details": plan["blocker_details"],
        "recovery_note": (
            "This manifest records pre-reset US registry/fact/source-plan evidence. Source package "
            "identities are preserved by reset; authoritative XML/ZIP files remain the replay source."
        ),
    }


def _write_manifest(raw_root: Path, plan: dict[str, Any]) -> tuple[Path, str]:
    directory = raw_root / MANIFEST_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    path = directory / f"us_clean_rebuild_pre_reset_{timestamp}.json"
    temporary = path.with_suffix(".json.tmp")
    payload = _manifest_payload(plan)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path, digest


def _truncate_us_fact_tables() -> None:
    client = clickhouse_client()
    for table in ALL_TABLE_KEYS:
        client.command(f"TRUNCATE TABLE markorbit_facts.{table} SYNC")


def _reset_registry_rows(reset_rows: list[dict[str, Any]]) -> None:
    if not reset_rows:
        return
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for row in reset_rows:
                cur.execute(
                    """
                    UPDATE control.source_package
                    SET file_name = %s,
                        file_path = %s,
                        file_size = %s,
                        package_kind = %s,
                        partition_dimension = %s,
                        partition_value = %s,
                        source_period_start = %s,
                        source_period_end = %s,
                        source_sequence = %s,
                        source_rank = %s,
                        status = 'REGISTERED',
                        profile = '{}'::jsonb,
                        schema_version = %s,
                        archived_path = NULL,
                        processed_at = NULL,
                        error_message = NULL,
                        last_seen_at = now()
                    WHERE package_id = %s
                      AND jurisdiction = 'US'
                      AND sha256 = %s
                    """,
                    (
                        row["file_name"],
                        row["file_path"],
                        row["file_size"],
                        row["package_kind"],
                        row["partition_dimension"],
                        row["partition_value"],
                        row["source_period_start"],
                        row["source_period_end"],
                        row["source_sequence"],
                        row["source_rank"],
                        US_SCHEMA_VERSION,
                        row["package_id"],
                        row["sha256"],
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(
                        f"US registry reset lost package identity: {row['package_id']}"
                    )
        conn.commit()


def _post_reset_registry_status() -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in list_us_replay_registry():
        status = str(row.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def apply_reset(
    raw_root: Path,
    *,
    expected_history_parts: int,
    confirmation: str,
    deep_source_test: bool = False,
) -> dict[str, Any]:
    if confirmation != RESET_CONFIRMATION:
        raise ValueError(
            f"Destructive US reset requires exact confirmation {RESET_CONFIRMATION!r}"
        )

    result: dict[str, Any] = {
        "status": "UNKNOWN",
        "reset_version": RESET_VERSION,
        "manifest_path": None,
        "manifest_sha256": None,
    }
    with us_ingestion_guard() as acquired:
        if not acquired:
            return {**result, "status": "BUSY"}

        ensure_us_m1_schema()
        plan = build_reset_plan(
            raw_root,
            expected_history_parts=expected_history_parts,
            deep_source_test=deep_source_test,
        )
        result["plan"] = plan
        if plan["status"] == "BLOCKED":
            return {**result, "status": "BLOCKED"}
        if plan["status"] == "NOOP":
            return {
                **result,
                "status": "NOOP",
                "post_table_counts": plan["table_counts"],
            }

        # Rebuild evidence is persisted before the first destructive ClickHouse operation.
        manifest_path, manifest_sha = _write_manifest(raw_root, plan)
        result["manifest_path"] = str(manifest_path)
        result["manifest_sha256"] = manifest_sha

        # Truncate facts first. If the later PostgreSQL reset fails, old SUCCESS statuses remain,
        # which is fail-closed: replay cannot start until reset is re-run and completes.
        _truncate_us_fact_tables()
        _reset_registry_rows(plan["reset_rows"])

        post_counts = _table_counts()
        if any(post_counts.values()):
            raise RuntimeError(f"US clean rebuild reset left fact rows: {post_counts}")
        registry_statuses = _post_reset_registry_status()
        unexpected = {
            status: count
            for status, count in registry_statuses.items()
            if status != "REGISTERED" and count
        }
        if unexpected:
            raise RuntimeError(
                f"US clean rebuild reset left non-REGISTERED package states: {unexpected}"
            )

        return {
            **result,
            "status": "RESET_COMPLETE",
            "post_table_counts": post_counts,
            "post_registry_status_counts": registry_statuses,
            "next_step": "Run deterministic US replay dry-run, then explicit apply.",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded US-only clean rebuild reset")
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--deep-source-test", action="store_true")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the destructive US-only reset. Without this flag the command is dry-run.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Exact destructive confirmation token; required with --apply: {RESET_CONFIRMATION}",
    )
    args = parser.parse_args()
    if args.expected_history_parts < 1:
        parser.error("--expected-history-parts must be at least 1")
    if args.apply and args.confirm != RESET_CONFIRMATION:
        parser.error(f"--apply requires --confirm {RESET_CONFIRMATION}")

    raw_root = get_settings().raw_data_root
    report = (
        apply_reset(
            raw_root,
            expected_history_parts=args.expected_history_parts,
            confirmation=args.confirm,
            deep_source_test=args.deep_source_test,
        )
        if args.apply
        else build_reset_plan(
            raw_root,
            expected_history_parts=args.expected_history_parts,
            deep_source_test=args.deep_source_test,
        )
    )
    report["mode"] = "APPLY" if args.apply else "DRY_RUN"
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
