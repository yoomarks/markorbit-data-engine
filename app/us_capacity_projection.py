from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PROJECTION_VERSION = "US_FULL_CORPUS_CAPACITY_PROJECTION_V1"


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer byte/count value")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer byte value")
    return value


def _percent(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if result < 0.0 or result >= 100.0:
        raise ValueError(f"{field} must be >= 0 and < 100")
    return result


def _byte_map(value: Any, field: str) -> dict[str, int]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} must be a non-empty object")
    result: dict[str, int] = {}
    for raw_name, raw_bytes in value.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError(f"{field} keys must be non-empty strings")
        name = raw_name.strip()
        if name in result:
            raise ValueError(f"{field} contains duplicate normalized key {name!r}")
        result[name] = _non_negative_int(raw_bytes, f"{field}.{name}")
    if not any(result.values()):
        raise ValueError(f"{field} must contain at least one positive byte value")
    return dict(sorted(result.items()))


def _ceil_ratio(total: int, numerator: int, denominator: int) -> int:
    return (total * numerator + denominator - 1) // denominator


def evaluate_projection(payload: dict[str, Any]) -> dict[str, Any]:
    corpus = payload.get("corpus")
    capacity = payload.get("capacity")
    policy = payload.get("policy") or {}
    pilot = payload.get("pilot")
    issues: list[dict[str, Any]] = []

    if not isinstance(corpus, dict):
        return _blocked([{"type": "CORPUS_REQUIRED"}])
    if not isinstance(capacity, dict):
        return _blocked([{"type": "CAPACITY_REQUIRED"}])

    try:
        corpus_raw_bytes = _positive_int(corpus.get("raw_bytes"), "corpus.raw_bytes")
        source_identity = corpus.get("source_identity")
        if not isinstance(source_identity, str) or not source_identity.strip():
            raise ValueError("corpus.source_identity is required")

        hot_free_bytes = _positive_int(capacity.get("hot_free_bytes"), "capacity.hot_free_bytes")
        hot_total_bytes = _positive_int(capacity.get("hot_total_bytes"), "capacity.hot_total_bytes")
        cold_free_bytes = _positive_int(capacity.get("cold_free_bytes"), "capacity.cold_free_bytes")
        cold_required_free_bytes = _non_negative_int(
            capacity.get("cold_required_free_bytes", 0), "capacity.cold_required_free_bytes"
        )
        hot_floor_percent = _percent(policy.get("hot_floor_percent", 30), "policy.hot_floor_percent")
        extra_hot_reserve_bytes = _non_negative_int(
            policy.get("extra_hot_reserve_bytes", 0), "policy.extra_hot_reserve_bytes"
        )
    except ValueError as exc:
        return _blocked([{"type": "INPUT_INVALID", "error": str(exc)}])

    if hot_free_bytes > hot_total_bytes:
        issues.append({"type": "HOT_FREE_EXCEEDS_TOTAL"})
    if cold_required_free_bytes > cold_free_bytes:
        issues.append({"type": "COLD_ALREADY_BELOW_REQUIRED_FREE"})
    if issues:
        return _blocked(issues)

    hot_floor_bytes = int(hot_total_bytes * (hot_floor_percent / 100.0))
    hot_budget_bytes = max(0, hot_free_bytes - hot_floor_bytes - extra_hot_reserve_bytes)
    cold_budget_bytes = max(0, cold_free_bytes - cold_required_free_bytes)

    base = {
        "projection_version": PROJECTION_VERSION,
        "read_only": True,
        "full_corpus_import_authorized": False,
        "corpus": {
            "source_identity": source_identity.strip(),
            "raw_bytes": corpus_raw_bytes,
        },
        "capacity": {
            "hot_free_bytes": hot_free_bytes,
            "hot_total_bytes": hot_total_bytes,
            "hot_floor_percent": hot_floor_percent,
            "hot_floor_bytes": hot_floor_bytes,
            "extra_hot_reserve_bytes": extra_hot_reserve_bytes,
            "hot_budget_bytes": hot_budget_bytes,
            "cold_free_bytes": cold_free_bytes,
            "cold_required_free_bytes": cold_required_free_bytes,
            "cold_budget_bytes": cold_budget_bytes,
        },
    }

    if not isinstance(pilot, dict):
        return {
            **base,
            "status": "PILOT_REQUIRED",
            "safe": False,
            "issues": [{"type": "BOUNDED_PILOT_RECEIPT_REQUIRED"}],
            "projection": None,
        }

    try:
        pilot_raw_bytes = _positive_int(pilot.get("raw_bytes"), "pilot.raw_bytes")
        pilot_warm_bytes = _non_negative_int(pilot.get("warm_bytes"), "pilot.warm_bytes")
        pilot_hot_bytes = _positive_int(pilot.get("hot_bytes"), "pilot.hot_bytes")
        hot_bytes_by_table_family = _byte_map(
            pilot.get("hot_bytes_by_table_family"), "pilot.hot_bytes_by_table_family"
        )
        pilot_rows = _positive_int(pilot.get("rows"), "pilot.rows")
        pilot_identity = pilot.get("receipt_identity")
        if not isinstance(pilot_identity, str) or not pilot_identity.strip():
            raise ValueError("pilot.receipt_identity is required")
        if sum(hot_bytes_by_table_family.values()) != pilot_hot_bytes:
            raise ValueError("pilot.hot_bytes_by_table_family must sum exactly to pilot.hot_bytes")
    except ValueError as exc:
        return {**base, **_blocked([{"type": "PILOT_INVALID", "error": str(exc)}])}

    if pilot_raw_bytes > corpus_raw_bytes:
        return {
            **base,
            **_blocked([{"type": "PILOT_RAW_BYTES_EXCEED_CORPUS_RAW_BYTES"}]),
        }

    projected_hot_bytes = _ceil_ratio(corpus_raw_bytes, pilot_hot_bytes, pilot_raw_bytes)
    projected_warm_bytes = _ceil_ratio(corpus_raw_bytes, pilot_warm_bytes, pilot_raw_bytes)
    projected_rows = _ceil_ratio(corpus_raw_bytes, pilot_rows, pilot_raw_bytes)
    projected_hot_by_table_family = {
        name: _ceil_ratio(corpus_raw_bytes, size, pilot_raw_bytes)
        for name, size in hot_bytes_by_table_family.items()
    }
    required_cold_and_warm_bytes = corpus_raw_bytes + projected_warm_bytes
    hot_safe = projected_hot_bytes <= hot_budget_bytes
    cold_safe = required_cold_and_warm_bytes <= cold_budget_bytes
    gate_issues: list[dict[str, Any]] = []
    if not hot_safe:
        gate_issues.append(
            {
                "type": "PROJECTED_HOT_EXCEEDS_BUDGET",
                "projected_hot_bytes": projected_hot_bytes,
                "hot_budget_bytes": hot_budget_bytes,
            }
        )
    if not cold_safe:
        gate_issues.append(
            {
                "type": "PROJECTED_COLD_AND_WARM_EXCEED_BUDGET",
                "required_cold_and_warm_bytes": required_cold_and_warm_bytes,
                "cold_budget_bytes": cold_budget_bytes,
            }
        )

    safe = hot_safe and cold_safe
    return {
        **base,
        "status": "GO" if safe else "NO_GO",
        "safe": safe,
        "full_corpus_import_authorized": safe,
        "issues": gate_issues,
        "pilot": {
            "receipt_identity": pilot_identity.strip(),
            "raw_bytes": pilot_raw_bytes,
            "warm_bytes": pilot_warm_bytes,
            "hot_bytes": pilot_hot_bytes,
            "hot_bytes_by_table_family": hot_bytes_by_table_family,
            "rows": pilot_rows,
        },
        "projection": {
            "method": "MEASURED_BOUNDED_PILOT_RAW_TO_STORAGE_RATIOS",
            "projected_hot_bytes": projected_hot_bytes,
            "projected_hot_bytes_by_table_family": projected_hot_by_table_family,
            "projected_warm_bytes": projected_warm_bytes,
            "projected_rows": projected_rows,
            "required_cold_raw_bytes": corpus_raw_bytes,
            "required_cold_and_warm_bytes": required_cold_and_warm_bytes,
            "hot_safe": hot_safe,
            "cold_safe": cold_safe,
            "physical_batch_or_package_shape_assumed_equal": False,
            "estimated_without_pilot": False,
        },
    }


def _blocked(issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "projection_version": PROJECTION_VERSION,
        "read_only": True,
        "status": "BLOCKED",
        "safe": False,
        "full_corpus_import_authorized": False,
        "issues": issues,
        "projection": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the measured US full-corpus Hot/Warm/Cold capacity gate"
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stdin", action="store_true")
    args = parser.parse_args()
    if bool(args.input) == bool(args.stdin):
        parser.error("provide exactly one of --input or --stdin")

    if args.stdin:
        payload = json.load(sys.stdin)
    else:
        assert args.input is not None
        with args.input.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)

    report = evaluate_projection(payload)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
