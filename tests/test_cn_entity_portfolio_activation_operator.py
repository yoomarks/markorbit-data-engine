from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cn import entity_portfolio_activation_operator as op


class FakeQueryClient:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def query(self, sql: str, settings=None):
        self.sql.append(sql)
        return SimpleNamespace(
            result_rows=[
                (
                    12,
                    77,
                    "a" * 64,
                    "10000001",
                    12345,
                )
            ]
        )


def test_entity_projection_is_application_bounded_and_lifecycle_ordered() -> None:
    sql = op.entity_projection_select(
        after_application="10000000",
        boundary_application="10009999",
        max_source_rank=77,
    )
    assert "FROM markorbit_facts.cn_trademark_relationship_event FINAL" in sql
    assert "source_rank <= 77" in sql
    assert "application_number > '10000000'" in sql
    assert "application_number <= '10009999'" in sql
    assert "PARTITION BY application_number, role, relation_key" in sql
    assert "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW" in sql
    assert "GROUP BY entity_id" not in sql


def test_source_snapshot_can_be_revalidated_to_frozen_rank() -> None:
    client = FakeQueryClient()
    stats = op.relationship_source_stats(client, max_source_rank=77)
    assert stats["row_count"] == 12
    assert stats["max_source_rank"] == 77
    assert "source_rank <= 77" in client.sql[0]


def test_execute_requires_exact_fresh_authority_before_client(tmp_path: Path) -> None:
    plan = {
        "version": op.PLAN_VERSION,
        "expected_main": "1" * 40,
        "implementation_sha": "1" * 40,
    }
    plan_sha = op._sha256(plan)
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps({"plan": plan, "plan_sha256": plan_sha}),
        encoding="utf-8",
    )
    with pytest.raises(PermissionError, match="fresh exact production authority"):
        op.execute_activation_plan(
            path,
            plan_sha=plan_sha,
            authority_token="GO #741 wrong",
            progress_path=tmp_path / "progress.json",
            receipt_path=tmp_path / "receipt.json",
            client=object(),
        )
