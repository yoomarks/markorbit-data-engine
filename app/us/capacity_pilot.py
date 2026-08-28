from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


RECEIPT_VERSION = "US_BOUNDED_CAPACITY_PILOT_RECEIPT_V1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# One deterministic US Application package is allowed to write only these tables.
# Keep this aligned with app.us.ingest.OUTPUT_PACKAGE_COLUMNS.
APPLICATION_OUTPUT_TABLES = {
    "us_case_current",
    "us_owner_current",
    "us_classification_current",
    "us_event_history",
    "us_statement_current",
    "us_correspondent_current",
    "us_design_search_current",
    "us_prior_registration_current",
    "us_foreign_application_current",
    "us_madrid_filing_current",
    "us_madrid_event_history",
    "us_case_observation_history",
}


def _family(table: str) -> str:
    if table == "us_case_current":
        return "case_core"
    if table in {"us_owner_current", "us_correspondent_current"}:
        return "party_contact"
    if table in {
        "us_classification_current",
        "us_statement_current",
        "us_design_search_current",
        "us_prior_registration_current",
        "us_foreign_application_current",
        "us_madrid_filing_current",
    }:
        return "case_detail"
    if table in {
        "us_event_history",
        "us_madrid_event_history",
        "us_case_observation_history",
    }:
        return "event_history"
    return "unexpected"


def _table_map(profile: dict[str, Any]) -> dict[str, dict[str, int]]:
    if profile.get("read_only") is not True:
        raise ValueError("storage profile must declare read_only=true")
    tables = profile.get("tables")
    if not isinstance(tables, list):
        raise ValueError("storage profile tables must be a list")
    result: dict[str, dict[str, int]] = {}
    for row in tables:
        if not isinstance(row, dict):
            raise ValueError("storage profile table rows must be objects")
        table = str(row.get("table") or "")
        if not table:
            raise ValueError("storage profile table name is required")
        bytes_on_disk = row.get("bytes_on_disk")
        rows = row.get("rows")
        if isinstance(bytes_on_disk, bool) or not isinstance(bytes_on_disk, int) or bytes_on_disk < 0:
            raise ValueError(f"invalid bytes_on_disk for {table}")
        if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
            raise ValueError(f"invalid rows for {table}")
        result[table] = {"bytes_on_disk": bytes_on_disk, "rows": rows}
    return result


def _source_for_step(dry_run: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    preflight = dry_run.get("preflight")
    if not isinstance(preflight, dict):
        raise ValueError("dry-run preflight is required")
    inventory = preflight.get("source_inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("sources"), list):
        raise ValueError("dry-run source inventory is required")
    matches = [
        row
        for row in inventory["sources"]
        if isinstance(row, dict)
        and str(row.get("path") or "") == str(step.get("path") or "")
        and str(row.get("sha256") or "").lower() == str(step.get("sha256") or "").lower()
    ]
    if len(matches) != 1:
        raise ValueError("exact next-step source identity was not found once in preflight inventory")
    source = matches[0]
    size = source.get("file_size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("pilot source file_size must be a positive integer")
    return source


def _blocked(engine_sha: str, issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "receipt_version": RECEIPT_VERSION,
        "engine_sha": engine_sha,
        "status": "BLOCKED",
        "safe": False,
        "projection_input_ready": False,
        "issues": issues,
        "pilot": None,
    }


def build_pilot_receipt(
    *,
    engine_sha: str,
    dry_run: dict[str, Any],
    replay: dict[str, Any],
    before_profile: dict[str, Any],
    after_profile: dict[str, Any],
) -> dict[str, Any]:
    engine_sha = engine_sha.strip().lower()
    if not SHA_RE.fullmatch(engine_sha):
        return _blocked(engine_sha, [{"type": "ENGINE_SHA_INVALID"}])

    issues: list[dict[str, Any]] = []
    if dry_run.get("status") != "READY" or dry_run.get("safe_to_execute") is not True:
        issues.append({"type": "DRY_RUN_NOT_READY", "status": dry_run.get("status")})
    next_step = dry_run.get("next_step")
    if not isinstance(next_step, dict):
        issues.append({"type": "DRY_RUN_NEXT_STEP_REQUIRED"})

    if replay.get("mode") != "APPLY":
        issues.append({"type": "REPLAY_NOT_APPLY_MODE"})
    if replay.get("status") not in {"PAUSED", "COMPLETE"}:
        issues.append({"type": "REPLAY_NOT_SUCCESSFUL", "status": replay.get("status")})
    if replay.get("processed_count") != 1:
        issues.append(
            {"type": "EXACTLY_ONE_PACKAGE_REQUIRED", "processed_count": replay.get("processed_count")}
        )
    processed = replay.get("processed")
    processed_row = processed[0] if isinstance(processed, list) and len(processed) == 1 else None
    if not isinstance(processed_row, dict):
        issues.append({"type": "PROCESSED_PACKAGE_RECEIPT_REQUIRED"})

    if issues:
        return _blocked(engine_sha, issues)

    assert isinstance(next_step, dict)
    assert isinstance(processed_row, dict)
    if (
        str(processed_row.get("sha256") or "").lower()
        != str(next_step.get("sha256") or "").lower()
        or str(processed_row.get("file_name") or "") != str(next_step.get("file_name") or "")
        or int(processed_row.get("sequence") or 0) != int(next_step.get("sequence") or 0)
    ):
        return _blocked(engine_sha, [{"type": "PROCESSED_PACKAGE_DOES_NOT_MATCH_DRY_RUN_NEXT_STEP"}])

    try:
        source = _source_for_step(dry_run, next_step)
        before = _table_map(before_profile)
        after = _table_map(after_profile)
    except ValueError as exc:
        return _blocked(engine_sha, [{"type": "EVIDENCE_INVALID", "error": str(exc)}])

    if before_profile.get("profile_version") != after_profile.get("profile_version"):
        return _blocked(engine_sha, [{"type": "STORAGE_PROFILE_VERSION_DRIFT"}])

    all_us_tables = sorted(
        table for table in set(before) | set(after) if table.startswith("us_")
    )
    table_deltas: dict[str, dict[str, int]] = {}
    unexpected_changed: list[str] = []
    negative_deltas: list[dict[str, Any]] = []
    for table in all_us_tables:
        before_row = before.get(table, {"bytes_on_disk": 0, "rows": 0})
        after_row = after.get(table, {"bytes_on_disk": 0, "rows": 0})
        byte_delta = after_row["bytes_on_disk"] - before_row["bytes_on_disk"]
        row_delta = after_row["rows"] - before_row["rows"]
        if byte_delta == 0 and row_delta == 0:
            continue
        table_deltas[table] = {"bytes_on_disk": byte_delta, "rows": row_delta}
        if table not in APPLICATION_OUTPUT_TABLES:
            unexpected_changed.append(table)
        if byte_delta < 0 or row_delta < 0:
            negative_deltas.append(
                {"table": table, "bytes_on_disk": byte_delta, "rows": row_delta}
            )

    if unexpected_changed:
        issues.append(
            {"type": "CONCURRENT_OR_UNEXPECTED_US_TABLE_CHANGE", "tables": unexpected_changed}
        )
    if negative_deltas:
        issues.append(
            {
                "type": "ACTIVE_PART_DELTA_NOT_STABLE_FOR_MEASUREMENT",
                "tables": negative_deltas,
            }
        )

    hot_bytes_by_family: dict[str, int] = {}
    hot_bytes = 0
    row_delta_total = 0
    for table, delta in table_deltas.items():
        if table not in APPLICATION_OUTPUT_TABLES:
            continue
        hot_bytes += max(0, delta["bytes_on_disk"])
        row_delta_total += max(0, delta["rows"])
        family = _family(table)
        hot_bytes_by_family[family] = hot_bytes_by_family.get(family, 0) + max(
            0, delta["bytes_on_disk"]
        )
    hot_bytes_by_family = {
        family: size for family, size in sorted(hot_bytes_by_family.items()) if size > 0
    }

    if hot_bytes <= 0:
        issues.append({"type": "NO_POSITIVE_HOT_BYTE_DELTA"})
    if row_delta_total <= 0:
        issues.append({"type": "NO_POSITIVE_ROW_DELTA"})
    if not hot_bytes_by_family:
        issues.append({"type": "NO_HOT_TABLE_FAMILY_DELTA"})

    metrics = processed_row.get("metrics")
    case_count = metrics.get("case_count") if isinstance(metrics, dict) else None
    if isinstance(case_count, bool) or not isinstance(case_count, int) or case_count <= 0:
        issues.append({"type": "PROCESSED_CASE_COUNT_REQUIRED"})

    if issues:
        return _blocked(engine_sha, issues)

    raw_bytes = int(source["file_size"])
    source_identity = f"sha256:{str(source['sha256']).lower()}"
    identity_payload = {
        "engine_sha": engine_sha,
        "source_identity": source_identity,
        "sequence": int(next_step["sequence"]),
        "raw_bytes": raw_bytes,
        "warm_bytes": 0,
        "hot_bytes": hot_bytes,
        "hot_bytes_by_table_family": hot_bytes_by_family,
        "rows": row_delta_total,
        "storage_profile_version": before_profile.get("profile_version"),
    }
    digest = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        "receipt_version": RECEIPT_VERSION,
        "engine_sha": engine_sha,
        "status": "PASS",
        "safe": True,
        "projection_input_ready": True,
        "issues": [],
        "pilot": {
            "receipt_identity": f"us-capacity-pilot:{digest}",
            "raw_bytes": raw_bytes,
            "warm_bytes": 0,
            "hot_bytes": hot_bytes,
            "hot_bytes_by_table_family": hot_bytes_by_family,
            "rows": row_delta_total,
        },
        "source": {
            "source_identity": source_identity,
            "file_name": source.get("file_name"),
            "package_kind": source.get("package_kind"),
            "partition_value": source.get("partition_value"),
            "sequence": next_step.get("sequence"),
            "case_count": case_count,
        },
        "measurement": {
            "method": "CLICKHOUSE_ACTIVE_SYSTEM_PARTS_PRE_POST_DELTA",
            "storage_profile_version": before_profile.get("profile_version"),
            "table_deltas": table_deltas,
            "warm_representation": "NONE_SEPARATE_FOR_US_APPLICATION_REPLAY_V1",
            "warm_bytes": 0,
            "source_retention": "SOURCE_FILE_MOVED_TO_RAW_ARCHIVE_WITHOUT_DUPLICATE_WARM_COPY",
            "negative_delta_policy": "BLOCK",
            "unexpected_us_table_change_policy": "BLOCK",
        },
    }


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one-package US capacity pilot receipt")
    parser.add_argument("--engine-sha", required=True)
    parser.add_argument("--dry-run", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--before-profile", type=Path, required=True)
    parser.add_argument("--after-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = build_pilot_receipt(
        engine_sha=args.engine_sha,
        dry_run=_load(args.dry_run),
        replay=_load(args.replay),
        before_profile=_load(args.before_profile),
        after_profile=_load(args.after_profile),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
