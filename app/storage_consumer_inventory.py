from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


CONTRACT_VERSION = "DATA_ENGINE_STORAGE_CONSUMER_CONTRACT_V1"
TARGET_TABLES = (
    "cn_goods_item_current",
    "cn_goods_item_observation",
    "cn_observed_event",
    "cn_case_party_current",
    "cn_case_party_relation_history",
)
SCAN_EXTENSIONS = {".py", ".ps1", ".sql", ".yml", ".yaml"}

# These decisions describe the current serving/storage contract only. They do not
# authorize a migration, DROP, OPTIMIZE, mutation, table swap, or live cutover.
TABLE_CONTRACTS: dict[str, dict[str, Any]] = {
    "cn_goods_item_current": {
        "current_tier_decision": "HOT_REQUIRED",
        "serving_contract": "CASE_DETAIL_PAYLOAD_AND_SUMMARY",
        "reconstructibility": "FULL_REPLAY_FROM_RETAINED_OFFICIAL_RAW_AUTHORITY",
        "storage_v2_evidence": (
            "Current rows retain first_source_package_id/first_source_rank provenance; "
            "that makes duplicate FIRST_OBSERVED goods-history baseline reconstructible, "
            "not the current serving table itself removable."
        ),
        "demotion_constraint": (
            "Do not demote/remove without a serving-compatible replacement projection "
            "for per-case goods payloads and summary semantics."
        ),
    },
    "cn_goods_item_observation": {
        "current_tier_decision": "WARM_CANDIDATE_REQUIRES_SUMMARY_REPLACEMENT",
        "serving_contract": "SUMMARY_COUNT_ONLY_IN_CURRENT_API",
        "reconstructibility": (
            "FIRST_OBSERVED_FROM_CURRENT_FIRST_SOURCE_PLUS_RAW;_REOBSERVED_IS_NO_OP"
        ),
        "storage_v2_evidence": (
            "Keep STATUS_CHANGED and ITEM_DETAILS_CHANGED true deltas; FIRST_OBSERVED "
            "and REOBSERVED baseline rows are compaction candidates."
        ),
        "demotion_constraint": (
            "Preserve the current /api/cn/summary count contract or replace it before "
            "moving retained delta history out of the Hot serving store."
        ),
    },
    "cn_observed_event": {
        "current_tier_decision": "HOT_WITH_COMPACTABLE_BASELINE",
        "serving_contract": "CASE_DETAIL_EVENT_PAYLOAD_AND_SUMMARY",
        "reconstructibility": (
            "SELECT_BASELINE_EVENTS_FROM_RAW_PLUS_CURRENT_FACTS;TRUE_DELTAS_AND_PARTY_EVENTS_RETAINED"
        ),
        "storage_v2_evidence": (
            "APPLICATION/GOODS_SCOPE/DERIVED_CASE baselines and first publication/term "
            "observations with empty old values are reconstructible candidates; events "
            "carrying prior-state evidence and party relation history remain durable."
        ),
        "demotion_constraint": (
            "The active event stream is directly returned by the CN case API. Only "
            "verified baseline subsets may be compacted without an API replacement."
        ),
    },
    "cn_case_party_current": {
        "current_tier_decision": "HOT_REQUIRED",
        "serving_contract": "CASE_DETAIL_PARTY_PAYLOAD_AND_SUMMARY",
        "reconstructibility": "FULL_REPLAY_FROM_RETAINED_OFFICIAL_RAW_AUTHORITY",
        "storage_v2_evidence": (
            "This is the current-state serving relation, distinct from duplicate legacy "
            "wide party-history rows."
        ),
        "demotion_constraint": (
            "Do not demote/remove without a serving-compatible current-party projection."
        ),
    },
    "cn_case_party_relation_history": {
        "current_tier_decision": "WARM_CANDIDATE_PENDING_VERIFICATION",
        "serving_contract": "NO_DIRECT_CASE_API_PAYLOAD_FOUND_BY_STATIC_CONTRACT_SCAN",
        "reconstructibility": "CANONICAL_PARTY_HISTORY_RETAINED_IN_CN_OBSERVED_EVENT",
        "storage_v2_evidence": (
            "Storage V2 suppresses duplicate SUPERSEDED and OBSERVED_CURRENT wide-history "
            "writes because canonical OWNER/CO_OWNER/AGENT observations and supersessions "
            "are already retained in cn_observed_event."
        ),
        "demotion_constraint": (
            "Existing legacy rows require consumer/recovery verification before any "
            "destructive compaction; static absence is not proof of no dynamic consumer."
        ),
    },
}


def _category(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    name = Path(normalized).name.lower()
    stem = Path(normalized).stem.lower()
    if normalized.startswith("scripts/"):
        return "operator"
    if normalized == "app/main_core.py" or name.endswith("_api.py") or "semantic_api" in name:
        return "serving_api"
    if stem.startswith("storage_v2") or stem in {
        "storage_audit",
        "storage_capacity_profile",
        "storage_headroom",
    }:
        return "storage_policy"
    if any(
        marker in stem
        for marker in ("audit", "validate", "acceptance", "checkpoint", "preflight")
    ):
        return "audit_acceptance"
    if normalized.startswith("app/cn/") and any(
        marker in stem
        for marker in ("publish", "ingest", "native_", "lifecycle", "replay", "schema")
    ):
        return "publisher_runtime"
    if normalized in {"app/jobs.py", "app/worker.py"}:
        return "publisher_runtime"
    return "runtime_other"


def _access_mode(table: str, context: str) -> str:
    qualified = rf"(?:markorbit_facts\.)?{re.escape(table)}"
    read = bool(re.search(rf"\b(?:FROM|JOIN)\s+{qualified}\b", context, re.IGNORECASE))
    write = bool(
        re.search(
            rf"\b(?:INSERT\s+INTO|ALTER\s+TABLE|CREATE\s+TABLE|DROP\s+TABLE|RENAME\s+TABLE)\s+{qualified}\b",
            context,
            re.IGNORECASE,
        )
        or re.search(
            rf"\bEXCHANGE\s+TABLES\s+{qualified}\b|\bEXCHANGE\s+TABLES\s+[^\n]+\s+AND\s+{qualified}\b",
            context,
            re.IGNORECASE,
        )
    )
    if read and write:
        return "read_write"
    if read:
        return "read"
    if write:
        return "write"
    return "reference"


def _source_files(repo_root: Path) -> list[Path]:
    result: list[Path] = []
    self_path = Path(__file__).resolve()
    for folder in (repo_root / "app", repo_root / "scripts"):
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SCAN_EXTENSIONS:
                continue
            if "__pycache__" in path.parts or path.resolve() == self_path:
                continue
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            result.append(path)
    return sorted(result)


def scan_table_consumers(repo_root: Path) -> dict[str, list[dict[str, Any]]]:
    root = repo_root.resolve()
    consumers: dict[str, list[dict[str, Any]]] = {table: [] for table in TARGET_TABLES}
    for path in _source_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        relative = path.resolve().relative_to(root).as_posix()
        category = _category(relative)
        for index, line in enumerate(lines):
            targets = [table for table in TARGET_TABLES if table in line]
            if not targets:
                continue
            context = "\n".join(lines[max(0, index - 5) : min(len(lines), index + 6)])
            for table in targets:
                consumers[table].append(
                    {
                        "path": relative,
                        "line": index + 1,
                        "category": category,
                        "access_mode": _access_mode(table, context),
                        "excerpt": line.strip()[:320],
                    }
                )
    return consumers


def build_inventory(repo_root: Path | None = None) -> dict[str, Any]:
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    scanned = scan_table_consumers(root)
    tables: list[dict[str, Any]] = []
    for table in TARGET_TABLES:
        consumers = scanned[table]
        category_counts = Counter(row["category"] for row in consumers)
        access_counts = Counter(row["access_mode"] for row in consumers)
        direct_serving_reads = [
            row
            for row in consumers
            if row["category"] == "serving_api"
            and row["access_mode"] in {"read", "read_write"}
        ]
        record = {
            "table": table,
            **TABLE_CONTRACTS[table],
            "consumer_count": len(consumers),
            "category_counts": dict(sorted(category_counts.items())),
            "access_mode_counts": dict(sorted(access_counts.items())),
            "direct_serving_read_count": len(direct_serving_reads),
            "direct_serving_reads": direct_serving_reads,
            "consumers": consumers,
        }
        tables.append(record)

    required_serving_anchors = {
        "cn_goods_item_current",
        "cn_observed_event",
        "cn_case_party_current",
    }
    missing_anchors = sorted(
        table
        for table in required_serving_anchors
        if not any(
            row["category"] == "serving_api"
            and row["access_mode"] in {"read", "read_write"}
            for row in scanned[table]
        )
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "PASS" if not missing_anchors else "REVIEW_REQUIRED",
        "source_scan_read_only": True,
        "repo_root": str(root),
        "required_serving_anchors": sorted(required_serving_anchors),
        "missing_serving_anchors": missing_anchors,
        "limitations": [
            "This is an exact-token static source scan; dynamic SQL can hide consumers.",
            "A missing static match is not proof that a table has no runtime consumer.",
            "Tier decisions are safety constraints and design evidence, not migration authorization.",
        ],
        "tables": tables,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Static, read-only storage consumer and reconstructibility contract audit."
    )
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    report = build_inventory(args.root)
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
    return 0 if report["status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
