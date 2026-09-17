from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.read_performance_baseline import (
    DEFAULT_QUERY_BUDGET,
    QueryBenchmarkCase,
    ReadPerformanceContractError,
    assert_read_only_sql,
    query_shape_audit,
    run_benchmark_case,
    run_benchmark_suite,
)
from app.read_query_capability import read_query_capability_contract


CAPABILITY_REGISTRY = Path("docs/integrations/markorbit/READ_QUERY_CAPABILITY_V1.json")


class FakeClient:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.queries: list[tuple[str, dict[str, object]]] = []

    def query(self, sql: str, *, settings: dict[str, object]):
        self.queries.append((sql, settings))
        if sql.startswith("EXPLAIN"):
            return SimpleNamespace(result_rows=[("PrimaryKey",), ("Granules: 1/100",)])
        if self.reject:
            raise RuntimeError("Code: 158. TOO_MANY_ROWS")
        return SimpleNamespace(
            result_rows=[("result",)],
            summary={"read_rows": "8192", "read_bytes": "4096", "result_rows": "1"},
        )


def _case(expected_outcome: str = "SUCCESS") -> QueryBenchmarkCase:
    return QueryBenchmarkCase(
        capability_id="exact_trademark",
        jurisdiction="US",
        query_shape="exact serial_number",
        sql="SELECT serial_number FROM markorbit_facts.us_case_current FINAL "
        "WHERE serial_number = '90000001' LIMIT 1",
        expected_outcome=expected_outcome,
        slo_p95_ms=150,
        repetitions=3,
    )


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO facts VALUES (1)",
        "SELECT 1; DROP TABLE facts",
        "SELECT 1 INTO OUTFILE 'result.tsv'",
        "WITH 1 AS value SELECT value -- comment",
        "SYSTEM FLUSH LOGS",
    ],
)
def test_benchmark_rejects_non_read_only_or_multi_statement_sql(sql: str) -> None:
    with pytest.raises(ReadPerformanceContractError):
        assert_read_only_sql(sql)


def test_query_shape_audit_records_final_offset_and_exact_count() -> None:
    observed = query_shape_audit("SELECT count(*) FROM facts FINAL ORDER BY id LIMIT 50 OFFSET 100")

    assert observed == {
        "uses_final": True,
        "uses_offset": True,
        "uses_exact_count": True,
    }


def test_benchmark_records_latency_io_plan_and_locked_budget(monkeypatch) -> None:
    samples = iter([1.000, 1.010, 2.000, 2.020, 3.000, 3.030])
    monkeypatch.setattr("app.read_performance_baseline.time.perf_counter", lambda: next(samples))
    client = FakeClient()

    result = run_benchmark_case(client, _case())

    assert result["observed_outcome"] == "SUCCESS"
    assert result["p50_ms"] == 20.0
    assert result["p95_ms"] == 29.0
    assert result["max_read_rows"] == 8192
    assert result["max_read_bytes"] == 4096
    assert result["result_count"] == 1
    assert result["query_cache_hit"] is False
    assert result["query_plan"] == ["PrimaryKey", "Granules: 1/100"]
    assert all(settings == DEFAULT_QUERY_BUDGET for _, settings in client.queries)


def test_expected_full_scan_is_rejected_by_budget() -> None:
    result = run_benchmark_case(FakeClient(reject=True), _case("BUDGET_REJECTED"))

    assert result["observed_outcome"] == "BUDGET_REJECTED"
    assert result["expectation_met"] is True
    assert result["samples"] == 0
    assert "TOO_MANY_ROWS" in result["budget_error"]


def test_suite_never_exposes_arbitrary_sql() -> None:
    result = run_benchmark_suite(FakeClient(), [_case()], target="accepted-us")

    assert result["arbitrary_sql"] is False
    assert result["all_expectations_met"] is True


def test_capability_registry_freezes_budget_and_required_query_shapes() -> None:
    registry = json.loads(CAPABILITY_REGISTRY.read_text(encoding="utf-8"))
    required = {
        "exact_trademark_application",
        "exact_trademark_registration",
        "applicant_owner_name_resolve",
        "agent_attorney_name_resolve",
        "current_entity_portfolio",
        "historical_entity_portfolio",
        "filing_date_range",
        "status_class_filtered_list",
        "relationship_timeline",
        "trademark_360",
        "assignment_lookup",
        "ttab_lookup",
    }

    assert registry["arbitrary_sql"] is False
    assert registry["unsupported_behavior"] == "CAPABILITY_ERROR_FAIL_CLOSED"
    assert registry["default_budget"] == {
        "max_execution_time_seconds": DEFAULT_QUERY_BUDGET["max_execution_time"],
        "max_rows_to_read": DEFAULT_QUERY_BUDGET["max_rows_to_read"],
        "max_bytes_to_read": DEFAULT_QUERY_BUDGET["max_bytes_to_read"],
        "max_result_rows": DEFAULT_QUERY_BUDGET["max_result_rows"],
        "max_threads": DEFAULT_QUERY_BUDGET["max_threads"],
    }
    assert required <= {capability["id"] for capability in registry["capabilities"]}
    assert registry == read_query_capability_contract()
