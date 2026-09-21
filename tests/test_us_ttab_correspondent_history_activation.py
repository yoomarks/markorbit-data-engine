from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.us.ttab_correspondent_history_activation as gate
from app.us.ttab_correspondent_history import READY_VERSION, TARGET_TABLE


MAIN = "a" * 40


def _plan() -> dict[str, object]:
    source = gate.SourceStats(
        joined_rows=10,
        relationship_count=8,
        normalized_name_count=4,
        serial_count=7,
        registration_count=3,
        max_source_rank=99,
        binding_sum="123",
        binding_xor="456",
    )
    return {
        "version": gate.PLAN_VERSION,
        "expected_main": MAIN,
        "implementation_sha": MAIN,
        "source_stats": gate.asdict(source),
        "target_precondition": {
            "lookup": {
                "exists": False,
                "visible_rows": 0,
                "relationship_count": 0,
                "normalized_name_count": 0,
                "max_source_rank": 0,
                "binding_sum": "0",
                "binding_xor": "0",
            },
            "ready_marker": None,
            "watermark": None,
        },
        "capacity_contract": {
            "hot_us": {"free_bytes": 1000, "total_bytes": 2000},
            "source_table_bytes": 100,
            "estimated_lookup_bytes_ceiling": 300,
            "minimum_free_ratio_after_estimate": 0.30,
            "projected_free_bytes": 700,
        },
        "target_schema": {
            "storage_policy": "hot_us_only",
            "table_ddl_sha256": gate.hashlib.sha256(
                gate.target_table_ddl().encode("utf-8")
            ).hexdigest(),
            "readiness_ddl_sha256": gate.hashlib.sha256(
                gate.readiness_table_ddl().encode("utf-8")
            ).hexdigest(),
            "watermark_ddl_sha256": gate.hashlib.sha256(
                gate.watermark_table_ddl().encode("utf-8")
            ).hexdigest(),
        },
        "acceptance": {
            "benchmark_runs": gate.BENCHMARK_RUNS,
            "indexed_historical_relationship_p95_ms": gate.SLO_P95_MS,
            "ready_version": READY_VERSION,
            "sample_rows": 200,
        },
        "mutation_scope": {
            "create_table": TARGET_TABLE,
            "create_readiness_table": gate.READINESS_TABLE,
            "create_watermark_table": gate.WATERMARK_TABLE,
            "backfill_sources": [
                gate.SOURCE_PARTY_TABLE,
                gate.SOURCE_PROPERTY_TABLE,
            ],
            "final_ready_version": READY_VERSION,
            "operation": "CREATE_INSERT_READY_ONLY",
        },
    }


def _write_plan(path: Path) -> str:
    plan = _plan()
    sha = gate._sha256(plan)
    path.write_text(
        json.dumps({"plan": plan, "plan_sha256": sha}),
        encoding="utf-8",
    )
    return sha
def test_authority_token_is_exact_issue_788():
    sha = "b" * 64
    assert gate.authority_token(sha) == (
        "GO #788 US-TTAB-CORRESPONDENT-HISTORY "
        + sha
        + " ACTIVATE"
    )


def test_load_plan_requires_exact_sha(tmp_path: Path):
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    assert gate.load_plan(path, sha)["expected_main"] == MAIN
    with pytest.raises(RuntimeError, match="plan SHA mismatch"):
        gate.load_plan(path, "c" * 64)


def test_backfill_uses_direct_ttab_correspondent_and_same_snapshot_party():
    sql = gate._backfill_sql()
    assert "p.correspondent_name" in sql
    assert "p.source_package_id = pr.source_package_id" in sql
    assert "p.source_rank = pr.source_rank" in sql
    assert "p.side = pr.party_side" in sql
    assert "p.ordinal = pr.party_ordinal" in sql
    assert "interlocutory_attorney" not in sql
    assert "us_assignment" not in sql.lower()
    assert "join_algorithm='grace_hash'" in sql
    assert "1 AS serving_generation" in sql
def test_schema_is_name_first_hot_us_and_ready_is_separate():
    table = gate.target_table_ddl()
    ready = gate.readiness_table_ddl()
    watermark = gate.watermark_table_ddl()
    assert "ORDER BY (normalized_name, serving_generation, observation_key)" in table
    assert "ReplacingMergeTree(source_rank)" in table
    assert "storage_policy = 'hot_us_only'" in table
    assert gate.READINESS_TABLE in ready
    assert "accepted_serving_generation UInt64" in ready
    assert gate.WATERMARK_TABLE in watermark
    assert "serving_generation UInt64" in watermark
    assert READY_VERSION not in table
    assert READY_VERSION not in ready


def test_prepare_contract_does_not_authorize_mutation(tmp_path: Path):
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    plan = gate.load_plan(path, sha)
    assert plan["mutation_scope"]["operation"] == "CREATE_INSERT_READY_ONLY"
    assert plan["acceptance"]["sample_rows"] == 200
    assert (
        plan["acceptance"]["indexed_historical_relationship_p95_ms"]
        == 400.0
    )


def test_benchmark_sql_is_accepted_by_select_only_read_guard():
    class Client:
        def __init__(self) -> None:
            self.sql = ""

        def query(self, sql: str, *, settings: dict[str, object]):
            self.sql = sql
            return type(
                "Result",
                (),
                {
                    "result_rows": [
                        ("91234567", "SERIAL:90123456", "b" * 64)
                    ]
                },
            )()

    client = Client()
    _elapsed_ms, rows = gate._page_benchmark(
        client,
        "jane q. counsel",
        1,
    )
    assert rows
    assert client.sql.lstrip().startswith("SELECT ")
    assert not client.sql.lstrip().startswith("WITH ")


def test_activation_watermark_read_uses_only_max_threads_budget():
    class Result:
        result_rows = [
            (3, 4070700000000002992, "11111111-1111-1111-1111-111111111111", "now")
        ]

    class Client:
        def __init__(self) -> None:
            self.settings_seen: list[dict[str, object]] = []

        def query(self, sql: str, *, settings: dict[str, object]):
            self.settings_seen.append(settings)
            if "system.tables" in sql:
                return type("Exists", (), {"result_rows": [(1,)]})()
            return Result()

    client = Client()
    watermark = gate._current_watermark(client)
    assert watermark is not None
    assert watermark["serving_generation"] == 3
    assert all(settings == {"max_threads": 1} for settings in client.settings_seen)


def test_prepare_accepts_exact_ready_recovery_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = gate.SourceStats(
        joined_rows=10,
        relationship_count=8,
        normalized_name_count=4,
        serial_count=7,
        registration_count=3,
        max_source_rank=99,
        binding_sum="123",
        binding_xor="456",
    )
    monkeypatch.setattr(gate, "source_stats", lambda _client: source)
    monkeypatch.setattr(
        gate,
        "lookup_stats",
        lambda _client: {
            "exists": True,
            "visible_rows": 10,
            "relationship_count": 8,
            "normalized_name_count": 4,
            "max_source_rank": 99,
            "binding_sum": "123",
            "binding_xor": "456",
        },
    )
    monkeypatch.setattr(gate, "ready_marker", lambda _client: READY_VERSION)
    monkeypatch.setattr(
        gate,
        "_current_watermark",
        lambda _client: {
            "serving_generation": 3,
            "source_max_rank": 99,
            "source_package_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    monkeypatch.setattr(
        gate,
        "capacity_contract",
        lambda _client, _source: {
            "hot_us": {"free_bytes": 1000, "total_bytes": 2000},
            "source_table_bytes": 100,
            "estimated_lookup_bytes_ceiling": 300,
            "minimum_free_ratio_after_estimate": 0.30,
            "projected_free_bytes": 700,
        },
    )

    envelope = gate.prepare_plan(
        tmp_path / "ready-recovery-plan.json",
        client=object(),
        main_sha_getter=lambda: MAIN,
    )

    precondition = envelope["plan"]["target_precondition"]
    assert precondition["ready_marker"] == READY_VERSION
    assert precondition["watermark"]["serving_generation"] == 3


def test_ready_recovery_reverifies_benchmark_and_runtime_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = gate.SourceStats(
        joined_rows=10,
        relationship_count=8,
        normalized_name_count=4,
        serial_count=7,
        registration_count=3,
        max_source_rank=99,
        binding_sum="123",
        binding_xor="456",
    )
    plan = _plan()
    plan["target_precondition"]["ready_marker"] = READY_VERSION
    plan["target_precondition"]["watermark"] = {
        "serving_generation": 3,
        "source_max_rank": 99,
        "source_package_id": "11111111-1111-1111-1111-111111111111",
    }
    plan_sha = "d" * 64

    class Client:
        def command(self, _sql: str) -> None:
            raise AssertionError("READY recovery must not mutate production")

    benchmark_generations: list[int] = []
    monkeypatch.setattr(gate, "load_plan", lambda _path, _sha: plan)
    monkeypatch.setattr(
        gate,
        "_assert_plan_live",
        lambda _plan, *, client, main_sha_getter: source,
    )
    monkeypatch.setattr(gate, "ready_marker", lambda _client: READY_VERSION)
    monkeypatch.setattr(
        gate,
        "_current_watermark",
        lambda _client: {
            "serving_generation": 3,
            "source_max_rank": 99,
            "source_package_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    monkeypatch.setattr(
        gate,
        "verify_completeness",
        lambda _client, _source: {"complete": True},
    )

    def _benchmark(_client, generation):
        benchmark_generations.append(generation)
        return {
            "passed": True,
            "normalized_name": "jane q. counsel",
            "runs": 7,
        }

    monkeypatch.setattr(gate, "benchmark_lookup", _benchmark)
    monkeypatch.setattr(
        gate,
        "execute_page",
        lambda _request, *, client: {
            "normalized_name": "jane q. counsel",
            "result_count": 1,
            "semantics": "DIRECT_OFFICIAL_USPTO_TTAB_PARTY_CORRESPONDENT_HISTORY",
        },
    )
    monkeypatch.setattr(
        gate,
        "accepted_us_target_read_client",
        lambda: object(),
    )

    receipt_path = tmp_path / "receipt.json"
    receipt = gate.execute_plan(
        tmp_path / "plan.json",
        plan_sha=plan_sha,
        authority=gate.authority_token(plan_sha),
        receipt_path=receipt_path,
        client=Client(),
        main_sha_getter=lambda: MAIN,
    )

    assert benchmark_generations == [3]
    assert receipt["status"] == "SUCCESS"
    assert receipt["replayed"] is True
    assert receipt["verification_only"] is True
    assert receipt["runtime_smoke"]["result_count"] == 1
