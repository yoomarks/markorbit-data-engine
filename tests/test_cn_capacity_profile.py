from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.cn.capacity_profile import (
    CLICKHOUSE_ACTIVE_PARTS_BY_DISK_SQL,
    CLICKHOUSE_DISKS_SQL,
    HARD_MIN_POST_SCALE_FREE_RATIO,
    PROFILE_VERSION,
    READ_ONLY_QUERIES,
    RECOMMENDED_MIN_POST_SCALE_FREE_RATIO,
    _aggregate_parts,
    _projection_gate,
    build_capacity_profile,
)


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "scripts" / "profile-cn-hot-warm-capacity.ps1"
GIB = 1024**3


class _FakeClickHouseClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, sql: str):
        self.queries.append(sql)
        if sql == CLICKHOUSE_ACTIVE_PARTS_BY_DISK_SQL:
            rows = [
                ("cn_goods_item_current", "default", 4, 1_000, 400 * GIB, 300, 800),
                ("cn_case_party_current", "default", 2, 500, 100 * GIB, 80, 200),
                ("cn_observed_event", "default", 3, 600, 120 * GIB, 90, 240),
                ("cn_goods_item_observation", "default", 1, 200, 50 * GIB, 40, 100),
                ("cn_unknown_history", "default", 1, 50, 10 * GIB, 8, 20),
            ]
        elif sql == CLICKHOUSE_DISKS_SQL:
            rows = [
                ("default", "/var/lib/clickhouse/", 1_200 * GIB, 2_000 * GIB, 0),
                ("cold", "/var/lib/clickhouse-cold/", 3_500 * GIB, 4_000 * GIB, 0),
            ]
        else:  # pragma: no cover
            raise AssertionError(f"unexpected query: {sql}")
        return SimpleNamespace(result_rows=rows)


def test_profile_is_metadata_only_and_reports_contract_totals() -> None:
    fake = _FakeClickHouseClient()
    report = build_capacity_profile(clickhouse_client_factory=lambda: fake)

    assert report["profile_version"] == PROFILE_VERSION
    assert report["read_only"] is True
    assert report["query_scope"] == "clickhouse_system_metadata_only"
    assert report["full_corpus_scan"] is False
    assert report["mutation_performed"] is False
    assert report["table_swap_performed"] is False
    assert fake.queries == [CLICKHOUSE_ACTIVE_PARTS_BY_DISK_SQL, CLICKHOUSE_DISKS_SQL]

    totals = report["active_totals"]
    assert totals["rows_from_parts"] == 2_350
    assert totals["bytes_on_disk"] == 680 * GIB
    assert totals["hot_contract_bytes"] == 620 * GIB
    assert totals["warm_candidate_bytes"] == 50 * GIB
    assert totals["unclassified_retain_as_is_bytes"] == 10 * GIB
    assert report["us_scale_out_gate"]["decision"] == "PROJECTION_REQUIRED"


def test_table_aggregation_preserves_disk_breakdown_and_placement_contract() -> None:
    tables = _aggregate_parts(
        [
            ("cn_goods_item_current", "default", 2, 100, 10, 8, 20),
            ("cn_goods_item_current", "cold", 1, 20, 2, 1, 4),
            ("cn_goods_item_observation", "default", 1, 30, 3, 2, 6),
        ]
    )
    goods_current = next(item for item in tables if item["table"] == "cn_goods_item_current")
    assert goods_current["bytes_on_disk"] == 12
    assert goods_current["rows_from_parts"] == 120
    assert goods_current["placement_contract"] == "HOT_REQUIRED_CURRENT_SERVING"
    assert {item["disk_name"] for item in goods_current["by_disk"]} == {"default", "cold"}

    observation = next(item for item in tables if item["table"] == "cn_goods_item_observation")
    assert observation["placement_contract"] == "WARM_AFTER_SUMMARY_EQUIVALENCE"


def test_projection_gate_go_warn_and_no_go() -> None:
    total = 2_000 * GIB
    free = 1_200 * GIB

    go = _projection_gate(total_bytes=total, free_bytes=free, projected_us_hot_bytes=500 * GIB)
    assert go["decision"] == "GO_WITHIN_PROJECTED_BUDGET"
    assert go["projected_post_scale_free_ratio"] == 0.35

    warn = _projection_gate(total_bytes=total, free_bytes=free, projected_us_hot_bytes=700 * GIB)
    assert warn["decision"] == "CONDITIONAL_WARN"
    assert warn["projected_post_scale_free_ratio"] == 0.25

    no_go = _projection_gate(total_bytes=total, free_bytes=free, projected_us_hot_bytes=850 * GIB)
    assert no_go["decision"] == "NO_GO"
    assert no_go["projected_post_scale_free_ratio"] == 0.175


def test_projection_budget_exposes_20_and_30_percent_floors() -> None:
    gate = _projection_gate(
        total_bytes=2_000 * GIB,
        free_bytes=1_200 * GIB,
        projected_us_hot_bytes=None,
    )
    assert HARD_MIN_POST_SCALE_FREE_RATIO == 0.20
    assert RECOMMENDED_MIN_POST_SCALE_FREE_RATIO == 0.30
    assert gate["hard_floor"]["max_additional_hot_bytes"] == 800 * GIB
    assert gate["recommended_floor"]["max_additional_hot_bytes"] == 600 * GIB


def test_query_contract_cannot_scan_fact_tables_or_mutate() -> None:
    joined = "\n".join(READ_ONLY_QUERIES).upper()
    assert "SYSTEM.PARTS" in joined
    assert "SYSTEM.DISKS" in joined
    assert "MARKORBIT_FACTS." not in joined

    forbidden = (
        " FINAL",
        "OPTIMIZE",
        "ALTER TABLE",
        "DELETE ",
        "INSERT ",
        "UPDATE ",
        "TRUNCATE",
        "DROP TABLE",
        "SYSTEM.PART_LOG",
    )
    for marker in forbidden:
        assert marker not in joined


def test_operator_wrapper_has_no_docker_or_service_lifecycle_actions() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "app.cn.capacity_profile" in text
    assert "ProjectedUsHotGiB" in text
    assert "reports" in text
    assert "full corpus scan" in lowered

    forbidden = (
        "docker ",
        "compose up",
        "compose run",
        "docker start",
        "docker stop",
        "restart-service",
        "optimize table",
        "2023_5.zip",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_missing_hot_disk_is_fail_closed_for_scale_out() -> None:
    class _NoHotDiskClient(_FakeClickHouseClient):
        def query(self, sql: str):
            if sql == CLICKHOUSE_DISKS_SQL:
                return SimpleNamespace(
                    result_rows=[("cold", "/var/lib/clickhouse-cold/", 3_500 * GIB, 4_000 * GIB, 0)]
                )
            return super().query(sql)

    report = build_capacity_profile(clickhouse_client_factory=_NoHotDiskClient)
    assert report["us_scale_out_gate"]["decision"] == "NO_GO"
    assert "default" in report["us_scale_out_gate"]["reason"]
