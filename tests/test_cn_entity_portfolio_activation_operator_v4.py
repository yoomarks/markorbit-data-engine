from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cn.entity_portfolio_activation_operator_v4 import (
    PLAN_VERSION,
    ActivationProgress,
    batch_action,
    execute_activation_plan,
    initial_progress,
    optimized_projection,
)
from app.cn.entity_portfolio_activation_operator import _sha256


def test_optimized_projection_elides_final() -> None:
    sql = optimized_projection(
        after_application="100",
        boundary_application="200",
        max_source_rank=123,
    )
    assert "cn_trademark_relationship_event FINAL" not in sql
    assert "cn_trademark_relationship_event" in sql
    assert "application_number > '100'" in sql
    assert "application_number <= '200'" in sql


def test_batch_action_distinguishes_recovery_insert_and_mismatch() -> None:
    expected = {"row_count": 10, "row_hash_xor": 9}
    assert batch_action(expected, expected) == "RECOVER"
    assert batch_action(expected, {"row_count": 0, "row_hash_xor": 0}) == "INSERT"
    assert batch_action(expected, {"row_count": 9, "row_hash_xor": 9}) == "MISMATCH"


def test_initial_progress_starts_from_frozen_resume_prefix() -> None:
    plan = {
        "resume_prefix": {
            "after_application_number": "1002171",
            "entity_rows_verified": 63971,
            "entity_hash_xor": 3398799554595918946,
        }
    }
    progress = initial_progress(plan, "a" * 64)
    assert progress == ActivationProgress(
        plan_sha256="a" * 64,
        after_application_number="1002171",
        entity_rows_verified=63971,
        entity_hash_xor=3398799554595918946,
    )


class TrapClient:
    def __getattr__(self, name):
        raise AssertionError(f"client must not be touched: {name}")


def test_apply_requires_exact_authority_before_client_access(tmp_path: Path) -> None:
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
