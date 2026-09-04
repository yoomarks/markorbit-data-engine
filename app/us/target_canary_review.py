from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from app.us.package_meta import infer_us_package_descriptor
from app.us.target_canary import (
    APPLICATION_CANARY_TABLES,
    TARGET_DATABASE,
    TARGET_DISTRO,
    TARGET_NATIVE_HOST,
    TARGET_NATIVE_PORT,
    TARGET_STORAGE_POLICY,
    build_target_schema_manifest,
    deterministic_package_id,
    freeze_package,
    package_column_for_table,
)


STAGE1_REVIEW_VERSION = "US_TARGET_CANARY_STAGE1_REVIEW_V1"
STAGE1_SOURCE_DECISION = "US_APPLICATION_CANARY_SOURCE_AND_SCHEMA_FROZEN"
FINAL_READY_DECISION = "BOUNDED_US_APPLICATION_CANARY_REVIEW_READY_FOR_OPERATOR_GO"
EXPECTED_HISTORY_PARTS = 91
EXPECTED_SUCCESS_PREFIX_COUNT = 1
EXPECTED_REMAINING_COUNT = 309
EXPECTED_NEXT_SEQUENCE = 2
PILOT_SEQUENCE = 1
PILOT_FILE_NAME = "apc18840407-20251231-01.zip"
PILOT_SHA256 = "9b65bdcb80c2bdd6efa6869432771c30613bed6dc8efd3d4589e2fd8b334b062"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"US target canary review expected object: {field}")
    return value


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuntimeError(f"US target canary review expected list: {field}")
    return value


def _source_inventory_row(plan: dict[str, Any], next_step: dict[str, Any]) -> dict[str, Any]:
    preflight = _object(plan.get("preflight"), "preflight")
    inventory = _object(preflight.get("source_inventory"), "preflight.source_inventory")
    sources = _list(inventory.get("sources"), "preflight.source_inventory.sources")
    digest = str(next_step.get("sha256") or "").lower()
    path = str(next_step.get("path") or "")
    matches = [
        row
        for row in sources
        if isinstance(row, dict)
        and str(row.get("sha256") or "").lower() == digest
        and str(row.get("path") or "") == path
    ]
    _require(
        len(matches) == 1,
        "US target canary next package must map to exactly one authoritative source inventory row",
    )
    return dict(matches[0])


def _validate_plan_continuity(plan: dict[str, Any]) -> dict[str, Any]:
    _require(str(plan.get("mode") or "") == "DRY_RUN", "US replay plan must be DRY_RUN")
    _require(str(plan.get("status") or "") == "READY", "US replay plan must be READY")
    _require(bool(plan.get("safe_to_execute")), "US replay plan is not safe_to_execute")
    _require(
        int(plan.get("expected_history_parts") or 0) == EXPECTED_HISTORY_PARTS,
        "US replay plan ExpectedHistoryParts drifted from 91",
    )
    _require(
        int(plan.get("success_prefix_count") or -1) == EXPECTED_SUCCESS_PREFIX_COUNT,
        "US replay plan successful prefix is no longer exactly one package",
    )
    _require(
        int(plan.get("remaining_count") or -1) == EXPECTED_REMAINING_COUNT,
        "US replay plan remaining package count is no longer 309",
    )

    steps = _list(plan.get("steps"), "steps")
    _require(len(steps) >= 2, "US replay plan must contain at least two deterministic steps")
    first = _object(steps[0], "steps[0]")
    _require(int(first.get("sequence") or 0) == PILOT_SEQUENCE, "US pilot sequence drifted")
    _require(str(first.get("file_name") or "") == PILOT_FILE_NAME, "US pilot file identity drifted")
    _require(
        str(first.get("sha256") or "").lower() == PILOT_SHA256,
        "US pilot SHA-256 identity drifted",
    )
    _require(str(first.get("registry_status") or "") == "SUCCESS", "US pilot is no longer SUCCESS")
    _require(str(first.get("action") or "") == "SKIP_SUCCESS", "US pilot is no longer a skipped success")

    next_step = _object(plan.get("next_step"), "next_step")
    _require(
        int(next_step.get("sequence") or 0) == EXPECTED_NEXT_SEQUENCE,
        "US Application canary next deterministic sequence is not 2",
    )
    _require(
        str(next_step.get("registry_status") or "") == "UNREGISTERED",
        "US Application canary next package is already registered; review before proceeding",
    )
    _require(
        str(next_step.get("action") or "") == "REGISTER_AND_INGEST",
        "US Application canary next package action is not REGISTER_AND_INGEST",
    )
    _require(
        str(next_step.get("location") or "") == "incoming",
        "US Application canary next package is not in incoming source storage",
    )
    _require(
        next_step.get("registry_package_id") is None,
        "US Application canary next package unexpectedly has a registry package id",
    )
    return dict(next_step)


def parse_source_schema_jsonl(lines: Iterable[str]) -> dict[str, str]:
    show_create: dict[str, str] = {}
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise RuntimeError("source schema JSONL row must be an object")
        name = str(row.get("name") or "")
        create = str(row.get("create_table_query") or "")
        if not name or not create:
            raise RuntimeError("source schema JSONL row is missing name/create_table_query")
        full_name = name if "." in name else f"{TARGET_DATABASE}.{name}"
        if full_name in show_create:
            raise RuntimeError(f"duplicate source schema row: {full_name}")
        show_create[full_name] = create

    required = set(APPLICATION_CANARY_TABLES)
    supplied = set(show_create)
    missing = sorted(required - supplied)
    extra = sorted(supplied - required)
    if missing or extra:
        raise RuntimeError(
            f"source Application schema set mismatch: missing={missing} extra={extra}"
        )
    return show_create


def build_stage1_source_review(
    plan: dict[str, Any],
    source_schema_rows: Iterable[str],
) -> dict[str, Any]:
    next_step = _validate_plan_continuity(plan)
    source = _source_inventory_row(plan, next_step)
    path = Path(str(next_step["path"]))
    descriptor = infer_us_package_descriptor(path)
    _require(descriptor.package_kind != "UNKNOWN", "US canary package descriptor is UNKNOWN")
    _require(
        descriptor.package_kind == str(next_step.get("package_kind") or ""),
        "US canary descriptor package kind differs from replay plan",
    )
    _require(
        descriptor.partition_value == str(next_step.get("partition_value") or ""),
        "US canary descriptor partition differs from replay plan",
    )
    _require(
        int(source.get("file_size") or -1) >= 0,
        "US canary source inventory is missing file_size",
    )

    sequence = int(next_step["sequence"])
    source_rank = descriptor.source_rank(sequence)
    package = freeze_package(
        path,
        expected_size=int(source["file_size"]),
        expected_sha256=str(next_step["sha256"]),
        package_kind=descriptor.package_kind,
        source_rank=source_rank,
        source_effective_date=descriptor.source_period_end,
        package_id=deterministic_package_id(str(next_step["sha256"])),
    )

    show_create = parse_source_schema_jsonl(source_schema_rows)
    manifest = build_target_schema_manifest(show_create)

    return {
        "review_version": STAGE1_REVIEW_VERSION,
        "decision": STAGE1_SOURCE_DECISION,
        "final_ready_decision_if_host_gates_pass": FINAL_READY_DECISION,
        "mode": "READ_ONLY_REVIEW",
        "continuity": {
            "expected_history_parts": EXPECTED_HISTORY_PARTS,
            "success_prefix_count": EXPECTED_SUCCESS_PREFIX_COUNT,
            "remaining_count": EXPECTED_REMAINING_COUNT,
            "pilot": {
                "sequence": PILOT_SEQUENCE,
                "file_name": PILOT_FILE_NAME,
                "sha256": PILOT_SHA256,
            },
            "next_sequence": sequence,
            "next_registry_status": next_step["registry_status"],
            "next_action": next_step["action"],
        },
        "package": {
            **package.as_dict(),
            "sequence": sequence,
            "partition_dimension": descriptor.partition_dimension,
            "partition_value": descriptor.partition_value,
            "source_period_start": (
                descriptor.source_period_start.isoformat()
                if descriptor.source_period_start is not None
                else None
            ),
            "source_period_end": (
                descriptor.source_period_end.isoformat()
                if descriptor.source_period_end is not None
                else None
            ),
            "source_sequence": descriptor.source_sequence,
            "source_rank": source_rank,
            "location": next_step["location"],
        },
        "target": {
            "distro": TARGET_DISTRO,
            "native_host": TARGET_NATIVE_HOST,
            "native_port": TARGET_NATIVE_PORT,
            "database": TARGET_DATABASE,
            "storage_policy": TARGET_STORAGE_POLICY,
            "required_tables": list(APPLICATION_CANARY_TABLES),
            "package_columns": {
                table: package_column_for_table(table) for table in APPLICATION_CANARY_TABLES
            },
            "first_canary_requires_all_required_tables_absent": True,
        },
        "schema_manifest": manifest,
        "safety": {
            "source_file_preserved": True,
            "registry_mutation_performed": False,
            "target_mutation_performed": False,
            "docker_mutation_performed": False,
            "cn_mutation_performed": False,
            "full_corpus_authorized": False,
            "stage2_go_consumed": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze the read-only source/package/schema portion of #526 Stage 1 review"
    )
    parser.add_argument("--plan-json", type=Path, required=True)
    parser.add_argument("--source-schema-jsonl", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()

    plan = json.loads(args.plan_json.read_text(encoding="utf-8-sig"))
    if not isinstance(plan, dict):
        raise RuntimeError("US replay plan JSON root must be an object")
    with args.source_schema_jsonl.open("r", encoding="utf-8-sig") as stream:
        review = build_stage1_source_review(plan, stream)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(review, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(review, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
