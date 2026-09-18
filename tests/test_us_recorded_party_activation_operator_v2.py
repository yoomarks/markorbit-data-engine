from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cn.entity_portfolio_activation_operator import _sha256
from app.us.recorded_party_activation_operator_v2 import (
    PLAN_VERSION,
    ActivationProgress,
    _batch_action,
    execute_activation_plan,
    load_progress,
)

from app.us.recorded_party_schema_activation_operator import (
    PLAN_VERSION as SCHEMA_PLAN_VERSION,
    apply_schema,
)


def test_batch_action_distinguishes_recover_insert_and_mismatch():
    expected = {"row_count": 10, "row_hash_xor": 7}
    assert _batch_action(expected, expected) == "RECOVER"
    assert _batch_action(expected, {"row_count": 0, "row_hash_xor": 0}) == "INSERT"
    assert _batch_action(expected, {"row_count": 9, "row_hash_xor": 7}) == "MISMATCH"


def test_progress_round_trip_supports_empty_serial_boundary(tmp_path: Path):
    progress = ActivationProgress(
        plan_sha256="a" * 64,
        stage="TTAB",
        after_serial_number="",
        serial_started=True,
        rows_verified=12,
        row_hash_xor=34,
        batches_verified=1,
    )
    path = tmp_path / "progress.json"
    path.write_text(
        json.dumps(
            {
                "version": "US_RECORDED_PARTY_HISTORY_ACTIVATION_PROGRESS_V2",
                "progress": progress.__dict__ if hasattr(progress, "__dict__") else {
                    "plan_sha256": progress.plan_sha256,
                    "stage": progress.stage,
                    "after_serial_number": progress.after_serial_number,
                    "serial_started": progress.serial_started,
                    "rows_verified": progress.rows_verified,
                    "row_hash_xor": progress.row_hash_xor,
                    "batches_verified": progress.batches_verified,
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = load_progress(path, "a" * 64)
    assert loaded.after_serial_number == ""
    assert loaded.serial_started is True
    assert loaded.stage == "TTAB"


class TrapClient:
    def __getattr__(self, name):
        raise AssertionError(f"client must not be touched: {name}")


def test_apply_requires_exact_authority_before_client_access(tmp_path: Path):
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


def test_schema_apply_requires_independent_exact_authority_before_client_access(
    tmp_path: Path,
):
    plan = {"version": SCHEMA_PLAN_VERSION}
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    path = tmp_path / "schema-plan.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(PermissionError, match="schema authority"):
        apply_schema(
            path,
            plan_sha=envelope["plan_sha256"],
            authority_token="GO #741 US recorded party history activation " + envelope["plan_sha256"],
            receipt_path=tmp_path / "schema-receipt.json",
            client=TrapClient(),
        )
