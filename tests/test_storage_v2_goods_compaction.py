from pathlib import Path

import pytest

from app.cn.storage_v2_goods import GoodsObservationDeltaClient
from app.cn.storage_v2_goods_compaction import build_plan


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _Delegate:
    def __init__(self):
        self.commands: list[str] = []

    def command(self, sql: str, *args, **kwargs):
        self.commands.append(sql)
        return None


class _PlanClient:
    def query(self, sql: str):
        if "GROUP BY transition_type" in sql:
            return _Result(
                [
                    ("FIRST_OBSERVED", 1000),
                    ("REOBSERVED", 2),
                    ("STATUS_CHANGED", 4),
                    ("ITEM_DETAILS_CHANGED", 1),
                ]
            )
        if "FROM markorbit_facts.cn_goods_item_current FINAL" in sql:
            return _Result([(999, 0)])
        if "FROM system.parts" in sql:
            return _Result([(100_700,)])
        if "FROM system.tables" in sql:
            return _Result([(0,)])
        raise AssertionError(sql)


def test_goods_observation_adapter_suppresses_first_observation_rows():
    delegate = _Delegate()
    client = GoodsObservationDeltaClient(delegate)
    sql = """
    INSERT INTO markorbit_facts.cn_goods_item_observation
    SELECT 1
    WHERE cur.application_number = ''
               OR (
                    cur.source_rank <= 10
                    AND (cur.record_hash != incoming.record_hash)
               )
    """

    client.command(sql)
    client.assert_rewrite_count(1)

    rewritten = delegate.commands[0]
    assert "WHERE cur.application_number != ''" in rewritten
    assert "WHERE cur.application_number = ''" not in rewritten


def test_goods_observation_adapter_fails_closed_on_sql_shape_drift():
    client = GoodsObservationDeltaClient(_Delegate())
    with pytest.raises(RuntimeError, match="expected CN goods baseline predicate"):
        client.command(
            "INSERT INTO markorbit_facts.cn_goods_item_observation SELECT 1 WHERE 1"
        )


def test_goods_compaction_plan_preserves_only_true_deltas():
    plan = build_plan(client=_PlanClient())

    assert plan["source_rows"] == 1007
    assert plan["removable_baseline_rows"] == 1002
    assert plan["keep_delta_rows"] == 5
    assert plan["unknown_transition_rows"] == 0
    assert plan["current_rows_missing_first_source"] == 0
    assert plan["safe_to_apply"] is True
    assert plan["estimated_reclaim_bytes"] > 100_000


def test_compaction_wrapper_never_starts_persistent_worker():
    source = Path("scripts/compact-cn-goods-history.ps1").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "docker compose run --rm --no-deps" in lowered
    assert "docker compose start worker" not in lowered
    assert "docker compose up -d worker" not in lowered
    assert "docker compose stop worker" in lowered
    assert "app.cn.storage_v2_goods_compaction" in source
