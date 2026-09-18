from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cn.entity_portfolio_activation_operator import _sha256
from app.us.recorded_party_activation_operator_v3 import (
    PLAN_VERSION,
    assignment_projection,
    execute_activation_plan,
    ttab_projection,
)
from app.us.recorded_party_schema_repair_operator import (
    PLAN_VERSION as REPAIR_PLAN_VERSION,
    _split_repair_sql,
    apply_repair,
)


class TrapClient:
    def __getattr__(self, name):
        raise AssertionError(f"client must not be touched: {name}")


def test_v3_relationship_hash_binds_observation_identity():
    assignment = assignment_projection(
        after_serial="",
        boundary_serial="99999999",
        started=False,
        max_rank=123,
    )
    assert "toString(p.observation_key)" in assignment
    assert "toString(prop.observation_key)" in assignment

    ttab = ttab_projection(
        after_serial="",
        boundary_serial="99999999",
        started=False,
        max_rank=456,
    )
    assert "toString(party.observation_key)" in ttab
    assert "toString(prop.observation_key)" in ttab


def test_v3_history_authority_is_fresh_and_exact(tmp_path: Path):
    plan = {"version": PLAN_VERSION}
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(PermissionError, match="authority"):
        execute_activation_plan(
            path,
            plan_sha=envelope["plan_sha256"],
            authority_token="NO",
            progress_path=tmp_path / "progress.json",
            receipt_path=tmp_path / "receipt.json",
            client=TrapClient(),
        )


def test_repair_authority_is_independent_from_history_authority(tmp_path: Path):
    plan = {"version": REPAIR_PLAN_VERSION}
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    path = tmp_path / "repair-plan.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    wrong = (
        "GO #741 US recorded party history activation "
        + envelope["plan_sha256"]
    )
    with pytest.raises(PermissionError, match="schema repair authority"):
        apply_repair(
            path,
            plan_sha=envelope["plan_sha256"],
            authority_token=wrong,
            receipt_path=tmp_path / "repair-receipt.json",
            client=TrapClient(),
        )


def test_repair_sql_is_exactly_bounded():
    sql = Path(
        "database/clickhouse/init/022_us_recorded_party_history_v2_repair.sql"
    ).read_text(encoding="utf-8")
    statements = _split_repair_sql(sql)
    assert len(statements) == 6
    assert statements[0].startswith(
        "DROP TABLE IF EXISTS markorbit_facts.us_assignment_recorded_party_relationship_mv"
    )
    assert statements[2].startswith(
        "TRUNCATE TABLE markorbit_facts.us_recorded_party_relationship_event"
    )
    assert "US_RECORDED_PARTY_HISTORY_SCHEMA_V2" in statements[-1]
