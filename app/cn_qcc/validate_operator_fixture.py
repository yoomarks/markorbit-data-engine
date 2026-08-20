from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile

from app.cn_qcc.operator import acquisition_state, expected_result_path, run_cycle
from app.cn_qcc.planner import create_batch_from_candidates
from app.cn_qcc.validate_fixture import _candidate, _write_result
from app.db import postgres_conn


def _remove_failed_fixture_batch() -> None:
    """Remove only the deliberately failed/open batch left by validate_fixture."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM acquisition.cn_qcc_task
                WHERE batch_id IN (
                    SELECT batch_id FROM acquisition.cn_qcc_batch
                    WHERE batch_key LIKE 'CN_QCC_FIXTURE_%'
                      AND status <> 'COMPLETED'
                )
                """
            )
            cur.execute(
                """
                DELETE FROM acquisition.cn_qcc_batch
                WHERE batch_key LIKE 'CN_QCC_FIXTURE_%'
                  AND status <> 'COMPLETED'
                """
            )
        conn.commit()


def _set_batch_key(batch_id: str, key: str) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE acquisition.cn_qcc_batch SET batch_key = %s WHERE batch_id = %s",
                (key, batch_id),
            )
        conn.commit()


def _read_single_task(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1, rows
    return rows[0]


def _batch_status(batch_id: str) -> str:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM acquisition.cn_qcc_batch WHERE batch_id = %s",
                (batch_id,),
            )
            row = cur.fetchone()
            assert row is not None
            return str(row["status"])


def main() -> None:
    _remove_failed_fixture_batch()

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        outgoing = root / "outgoing"
        incoming = root / "incoming"
        key = "CN_QCC_OPERATOR_FIXTURE"

        plan = create_batch_from_candidates(
            [_candidate()],
            capacity=10,
            refresh_days=180,
            backfill_bucket=12,
            backfill_entity_from="",
            backfill_entity_to="operator-cursor",
            backfill_bucket_exhausted=False,
        )
        _set_batch_key(plan.batch_id, key)

        state = acquisition_state(enabled=True, incoming_root=incoming)
        assert state.readiness == "READY_TO_EXPORT", state
        assert state.open_batch_id == plan.batch_id, state

        exported = run_cycle(
            enabled=True,
            capacity=10,
            refresh_days=180,
            outgoing_root=outgoing,
            incoming_root=incoming,
        )
        assert exported["action"] == "EXPORTED", exported
        task_path = outgoing / f"{key}.tasks.csv"
        assert task_path.is_file(), exported
        assert exported["state"]["readiness"] == "WAITING_RESULT", exported

        task = _read_single_task(task_path)
        result_path = expected_result_path(incoming, key)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        _write_result(result_path, task=task)

        ready = acquisition_state(enabled=True, incoming_root=incoming)
        assert ready.readiness == "RESULT_READY", ready

        ingested = run_cycle(
            enabled=True,
            capacity=10,
            refresh_days=180,
            outgoing_root=outgoing,
            incoming_root=incoming,
        )
        assert ingested["action"] == "INGESTED", ingested
        assert ingested["result"]["status"] == "COMPLETED", ingested
        assert ingested["state"]["readiness"] == "READY_TO_PLAN", ingested
        assert _batch_status(plan.batch_id) == "COMPLETED"

        empty = create_batch_from_candidates(
            [],
            capacity=10,
            refresh_days=180,
            backfill_bucket=13,
            backfill_entity_from="",
            backfill_entity_to="",
            backfill_bucket_exhausted=True,
        )
        _set_batch_key(empty.batch_id, "CN_QCC_OPERATOR_EMPTY_FIXTURE")
        assert empty.task_count == 0

        idle = run_cycle(
            enabled=True,
            capacity=10,
            refresh_days=180,
            outgoing_root=outgoing,
            incoming_root=incoming,
        )
        assert idle["action"] == "IDLE", idle
        assert idle["state"]["readiness"] == "READY_TO_PLAN", idle
        assert _batch_status(empty.batch_id) == "COMPLETED"

    print(
        json.dumps(
            {
                "status": "PASS",
                "operator_state_machine": True,
                "deterministic_result_handoff": True,
                "transactional_ingest_cycle": True,
                "empty_batch_auto_completion": True,
            }
        )
    )


if __name__ == "__main__":
    main()
