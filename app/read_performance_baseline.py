from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


READ_PERFORMANCE_BASELINE_VERSION = "FOUNDATION_READ_PERFORMANCE_BASELINE_V1"
DEFAULT_QUERY_BUDGET = {
    "max_threads": 1,
    "max_execution_time": 3,
    "max_rows_to_read": 1_000_000,
    "max_bytes_to_read": 268_435_456,
    "read_overflow_mode": "throw",
    "max_result_rows": 201,
    "result_overflow_mode": "throw",
    "use_query_cache": 0,
}

_READ_ONLY_SQL = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_SQL = re.compile(
    r"\b(ALTER|ATTACH|CREATE|DELETE|DETACH|DROP|INSERT|KILL|MOVE|OPTIMIZE|RENAME|"
    r"REPLACE|SYSTEM|TRUNCATE|UPDATE)\b|\bINTO\s+OUTFILE\b|;|--|/\*",
    re.IGNORECASE,
)
_BUDGET_ERRORS = (
    "TOO_MANY_ROWS",
    "TOO_MANY_BYTES",
    "TIMEOUT_EXCEEDED",
    "QUERY_WAS_CANCELLED",
    "RESULT_ROWS_LIMIT_EXCEEDED",
)


class ReadPerformanceContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QueryBenchmarkCase:
    capability_id: str
    jurisdiction: str
    query_shape: str
    sql: str
    expected_outcome: str
    slo_p95_ms: int | None
    repetitions: int = 7

    def __post_init__(self) -> None:
        if self.expected_outcome not in {"SUCCESS", "BUDGET_REJECTED"}:
            raise ReadPerformanceContractError("unsupported benchmark expected outcome")
        if self.jurisdiction not in {"CN", "US"}:
            raise ReadPerformanceContractError("unsupported benchmark jurisdiction")
        if not self.capability_id or not self.query_shape:
            raise ReadPerformanceContractError("benchmark identity is required")
        if not 1 <= self.repetitions <= 20:
            raise ReadPerformanceContractError("benchmark repetitions must be between 1 and 20")
        assert_read_only_sql(self.sql)


def assert_read_only_sql(sql: str) -> None:
    statement = str(sql or "").strip()
    if not _READ_ONLY_SQL.match(statement) or _FORBIDDEN_SQL.search(statement):
        raise ReadPerformanceContractError(
            "benchmark SQL must be one read-only SELECT/WITH statement"
        )


def query_shape_audit(sql: str) -> dict[str, bool]:
    upper = sql.upper()
    return {
        "uses_final": bool(re.search(r"\bFINAL\b", upper)),
        "uses_offset": bool(re.search(r"\bOFFSET\b", upper)),
        "uses_exact_count": bool(re.search(r"\bCOUNT\s*\(\s*\*\s*\)", upper)),
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def _summary_int(summary: Mapping[str, Any], key: str) -> int:
    value = summary.get(key, 0)
    return int(value or 0)


def _plan_lines(result: Any) -> list[str]:
    return [str(row[0]) for row in result.result_rows]


def _is_budget_error(error: Exception) -> bool:
    message = str(error).upper()
    return any(code in message for code in _BUDGET_ERRORS)


def run_benchmark_case(
    client: Any,
    case: QueryBenchmarkCase,
) -> dict[str, Any]:
    budget = dict(DEFAULT_QUERY_BUDGET)
    plan = _plan_lines(client.query(f"EXPLAIN indexes = 1 {case.sql}", settings=budget))
    elapsed_ms: list[float] = []
    read_rows: list[int] = []
    read_bytes: list[int] = []
    result_counts: list[int] = []
    error: str | None = None
    outcome = "SUCCESS"

    for _ in range(case.repetitions):
        started = time.perf_counter()
        try:
            result = client.query(case.sql, settings=budget)
        except Exception as exc:  # driver exception classes differ by transport
            if not _is_budget_error(exc):
                raise
            outcome = "BUDGET_REJECTED"
            error = str(exc)
            break
        elapsed_ms.append((time.perf_counter() - started) * 1_000)
        summary = dict(getattr(result, "summary", {}) or {})
        read_rows.append(_summary_int(summary, "read_rows"))
        read_bytes.append(_summary_int(summary, "read_bytes"))
        result_counts.append(
            _summary_int(summary, "result_rows") or len(getattr(result, "result_rows", []))
        )

    return {
        "capability_id": case.capability_id,
        "jurisdiction": case.jurisdiction,
        "query_shape": case.query_shape,
        "expected_outcome": case.expected_outcome,
        "observed_outcome": outcome,
        "expectation_met": outcome == case.expected_outcome,
        "slo_p95_ms": case.slo_p95_ms,
        "slo_met": (
            None
            if case.slo_p95_ms is None or not elapsed_ms
            else _percentile(elapsed_ms, 0.95) <= case.slo_p95_ms
        ),
        "samples": len(elapsed_ms),
        "p50_ms": _percentile(elapsed_ms, 0.50),
        "p95_ms": _percentile(elapsed_ms, 0.95),
        "max_read_rows": max(read_rows, default=0),
        "max_read_bytes": max(read_bytes, default=0),
        "result_count": max(result_counts, default=0),
        "query_cache_hit": False,
        "shape_audit": query_shape_audit(case.sql),
        "query_plan": plan,
        "budget_error": error,
    }


def run_benchmark_suite(
    client: Any,
    cases: Iterable[QueryBenchmarkCase],
    *,
    target: str,
) -> dict[str, Any]:
    results = [run_benchmark_case(client, case) for case in cases]
    return {
        "version": READ_PERFORMANCE_BASELINE_VERSION,
        "target": target,
        "budget": dict(DEFAULT_QUERY_BUDGET),
        "arbitrary_sql": False,
        "results": results,
        "all_expectations_met": all(result["expectation_met"] for result in results),
    }
