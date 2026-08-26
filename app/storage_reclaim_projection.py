from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.storage_audit import build_storage_audit
from app.storage_capacity_profile import build_live_capacity_profile
from app.storage_tier_decision import PROTECTED_CURRENT_TABLES


PROJECTION_VERSION = "DATA_ENGINE_STORAGE_RECLAIM_PROJECTION_V1"
PLANNING_METHOD = "CANDIDATE_ROW_SHARE_TIMES_ACTIVE_COMPRESSED_TABLE_BYTES"


def _table_index(capacity_profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("table")): dict(row)
        for row in capacity_profile.get("tables") or []
        if row.get("table")
    }


def _candidate_projection(
    table: str,
    candidate_rows: int,
    table_row: dict[str, Any] | None,
    *,
    policy: str,
) -> dict[str, Any]:
    if table_row is None:
        return {
            "table": table,
            "status": "TABLE_NOT_PRESENT_IN_CAPACITY_PROFILE",
            "candidate_rows": int(candidate_rows),
            "planning_estimate_bytes": None,
            "policy": policy,
        }

    total_rows = int(table_row.get("rows") or 0)
    table_bytes = int(table_row.get("bytes_on_disk") or 0)
    candidate_rows = int(candidate_rows)
    if candidate_rows < 0:
        raise ValueError(f"negative candidate rows for {table}: {candidate_rows}")
    if candidate_rows > total_rows:
        raise ValueError(
            f"candidate rows exceed active table rows for {table}: "
            f"candidate={candidate_rows} active={total_rows}"
        )

    share = (candidate_rows / total_rows) if total_rows else 0.0
    estimate = round(table_bytes * share) if total_rows else 0
    return {
        "table": table,
        "status": "PLANNING_ESTIMATE_AVAILABLE",
        "active_rows": total_rows,
        "active_bytes": table_bytes,
        "candidate_rows": candidate_rows,
        "candidate_row_share": share,
        "planning_estimate_bytes": estimate,
        "planning_estimate_method": PLANNING_METHOD,
        "measured_reclaimable_bytes": None,
        "planning_lower_bound_bytes": 0,
        "planning_upper_bound_bytes": table_bytes,
        "policy": policy,
        "warning": (
            "Compressed bytes are not uniform per row. This proportional estimate is for "
            "capacity planning only and is not deletion, mutation, or compaction authorization."
        ),
    }


def _deep_evidence_valid(deep_audit: dict[str, Any]) -> tuple[bool, list[str]]:
    violations: list[str] = []
    if deep_audit.get("read_only") is not True:
        violations.append("DEEP_AUDIT_NOT_MARKED_READ_ONLY")
    if deep_audit.get("mode") != "deep":
        violations.append("DEEP_AUDIT_MODE_REQUIRED")
    for key in (
        "cn_goods_item_observation",
        "cn_observed_event",
        "cn_case_party_relation_history",
    ):
        if not isinstance(deep_audit.get(key), dict):
            violations.append(f"MISSING_DEEP_AUDIT_SECTION:{key}")
    return not violations, sorted(violations)


def build_reclaim_projection(
    capacity_profile: dict[str, Any],
    deep_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tables = _table_index(capacity_profile)
    active_bytes = int(capacity_profile.get("active_bytes") or 0)
    active_rows = int(capacity_profile.get("active_rows") or 0)
    protected_bytes = sum(
        int((tables.get(table) or {}).get("bytes_on_disk") or 0)
        for table in PROTECTED_CURRENT_TABLES
    )

    base = {
        "projection_version": PROJECTION_VERSION,
        "read_only": True,
        "active_bytes": active_bytes,
        "active_rows": active_rows,
        "protected_current_tables": list(PROTECTED_CURRENT_TABLES),
        "protected_current_table_bytes": protected_bytes,
        "numeric_scope": "CN_ACCEPTED_CORPUS_ONLY",
        "us_projection": {
            "status": "NO_NUMERIC_ESTIMATE_WITHOUT_ACCEPTED_FULL_CORPUS_PROFILE",
            "reason": "Do not extrapolate US bytes from CN row or byte multipliers.",
        },
        "global_projection": {
            "status": "NO_NUMERIC_ESTIMATE_WITHOUT_JURISDICTION_CORPUS_EVIDENCE",
            "reason": (
                "Country schemas, source history, goods/event cardinality, and compression "
                "differ; CN is not a valid multiplier for global storage sizing."
            ),
        },
        "authorization": "NONE_READ_ONLY_PLANNING_EVIDENCE",
    }

    if deep_audit is None:
        return {
            **base,
            "status": "WAITING_DEEP_AUDIT_EVIDENCE",
            "candidates": [],
            "planning_reclaim_estimate_bytes": None,
            "planning_retained_active_bytes": None,
            "note": (
                "No deep audit was supplied. The command does not start a deep table scan "
                "implicitly; use a saved receipt or explicit --live-deep-audit opt-in."
            ),
        }

    valid, violations = _deep_evidence_valid(deep_audit)
    if not valid:
        return {
            **base,
            "status": "BLOCKED_INVALID_DEEP_AUDIT_EVIDENCE",
            "violations": violations,
            "candidates": [],
            "planning_reclaim_estimate_bytes": None,
            "planning_retained_active_bytes": None,
        }

    goods = deep_audit["cn_goods_item_observation"]
    events = deep_audit["cn_observed_event"]
    party = deep_audit["cn_case_party_relation_history"]

    goods_candidate_rows = int(goods.get("first_observed_rows") or 0) + int(
        goods.get("reobserved_rows") or 0
    )
    event_candidate_rows = int(events.get("reconstructible_baseline_candidate_rows") or 0)
    party_candidate_rows = int(party.get("observed_current_rows") or 0)

    try:
        candidates = [
            _candidate_projection(
                "cn_goods_item_observation",
                goods_candidate_rows,
                tables.get("cn_goods_item_observation"),
                policy="FIRST_OBSERVED_AND_REOBSERVED_BASELINE_HISTORY_ONLY",
            ),
            _candidate_projection(
                "cn_observed_event",
                event_candidate_rows,
                tables.get("cn_observed_event"),
                policy="VERIFIED_RECONSTRUCTIBLE_EVENT_BASELINE_SUBSET_ONLY",
            ),
            _candidate_projection(
                "cn_case_party_relation_history",
                party_candidate_rows,
                tables.get("cn_case_party_relation_history"),
                policy="UNCHANGED_OBSERVED_CURRENT_LEGACY_WIDE_HISTORY_ONLY",
            ),
        ]
    except ValueError as exc:
        return {
            **base,
            "status": "BLOCKED_DEEP_AUDIT_CAPACITY_MISMATCH",
            "violations": [str(exc)],
            "candidates": [],
            "planning_reclaim_estimate_bytes": None,
            "planning_retained_active_bytes": None,
        }

    estimate = sum(
        int(row["planning_estimate_bytes"] or 0)
        for row in candidates
        if row["status"] == "PLANNING_ESTIMATE_AVAILABLE"
    )
    estimate = min(estimate, active_bytes)
    retained = max(0, active_bytes - estimate)

    return {
        **base,
        "status": "PASS_PLANNING_PROJECTION_AVAILABLE",
        "deep_audit_version": deep_audit.get("audit_version"),
        "candidates": candidates,
        "planning_reclaim_estimate_bytes": estimate,
        "planning_retained_active_bytes": retained,
        "planning_reclaim_share": (estimate / active_bytes) if active_bytes else 0.0,
        "note": (
            "The byte estimate is deliberately proportional and conservative in authority: "
            "it quantifies planning potential but does not prove physical bytes that a future "
            "compaction would reclaim. Measure before/after parts on a reversible fixture or "
            "approved target operation before treating bytes as realized savings."
        ),
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conservative read-only CN storage reclaim/capacity projection."
    )
    parser.add_argument("--capacity-json", type=Path, default=None)
    parser.add_argument("--deep-audit-json", type=Path, default=None)
    parser.add_argument("--live-deep-audit", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--require-projection", action="store_true")
    args = parser.parse_args()

    if args.deep_audit_json is not None and args.live_deep_audit:
        parser.error("choose --deep-audit-json or --live-deep-audit, not both")

    capacity = (
        _read_json(args.capacity_json)
        if args.capacity_json is not None
        else build_live_capacity_profile()
    )
    deep_audit = None
    if args.deep_audit_json is not None:
        deep_audit = _read_json(args.deep_audit_json)
    elif args.live_deep_audit:
        deep_audit = build_storage_audit(deep=True)

    report = build_reclaim_projection(capacity, deep_audit)
    payload = json.dumps(
        report,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        sort_keys=True,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)

    if report["status"].startswith("BLOCKED_"):
        return 4
    if args.require_projection and report["status"] != "PASS_PLANNING_PROJECTION_AVAILABLE":
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
