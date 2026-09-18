from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cn import relationship_timeline_activation_operator as op
from app.cn.relationship_timeline import RELATIONSHIP_TIMELINE_READY_VERSION


class FakeClient:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries: list[str] = []

    def query(self, sql: str, settings=None):
        self.queries.append(sql)
        rows = self.rows.pop(0)
        return SimpleNamespace(result_rows=rows)


def _plan_file(tmp_path: Path) -> tuple[Path, str]:
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
    return path, plan_sha


def test_relationship_ready_version_requires_production_acceptance_v2() -> None:
    assert RELATIONSHIP_TIMELINE_READY_VERSION == "CN_RELATIONSHIP_TIMELINE_READY_V2"


def test_source_stats_can_be_frozen_to_rank() -> None:
    client = FakeClient(
        [[(270422729, 2000202608002993, "a" * 64, "G999998", 12345)]]
    )
    stats = op.source_stats(client, max_source_rank=2000202608002993)
    assert stats["row_count"] == 270422729
    assert stats["max_source_rank"] == 2000202608002993
    assert "source_rank <= 2000202608002993" in client.queries[0]
    assert "OWNER_RELATION_OBSERVED" in client.queries[0]


def test_cleanup_requires_distinct_exact_authority_before_client(tmp_path: Path) -> None:
    path, plan_sha = _plan_file(tmp_path)
    with pytest.raises(PermissionError, match="fixture-cleanup authority"):
        op.cleanup_fixture_rows(
            path,
            plan_sha=plan_sha,
            authority_token="GO #741 wrong",
            client=object(),
        )


def test_backfill_requires_distinct_exact_authority_before_client(tmp_path: Path) -> None:
    path, plan_sha = _plan_file(tmp_path)
    with pytest.raises(PermissionError, match="relationship activation authority"):
        op.execute_backfill(
            path,
            plan_sha=plan_sha,
            authority_token="GO #741 wrong",
            progress_path=tmp_path / "progress.json",
            receipt_path=tmp_path / "receipt.json",
            client=object(),
        )


def test_fixture_identity_is_narrow_and_explicit() -> None:
    assert op.FIXTURE_APPLICATION == "MO-TIMELINE-001"
    assert op.FIXTURE_KIND == "CN_FIXTURE"
    assert op.FIXTURE_FILE == "fixture.csv"
    assert op.FIXTURE_HASHES == ("a" * 64, "b" * 64, "c" * 64)
