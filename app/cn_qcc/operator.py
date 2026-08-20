from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from app.cn_qcc.exporter import export_batch
from app.cn_qcc.incoming import ingest_result
from app.cn_qcc.migrations import ensure_qcc_schema
from app.cn_qcc.planner import create_batch, plan_as_dict
from app.db import postgres_conn


OperatorAction = Literal[
    "DISABLED",
    "PLANNED_AND_EXPORTED",
    "EXPORTED",
    "WAITING_RESULT",
    "INGESTED",
    "IDLE",
]


@dataclass(frozen=True)
class AcquisitionState:
    enabled: bool
    readiness: str
    open_batch_id: str
    batch_key: str
    batch_status: str
    task_count: int
    export_path: str
    result_expected_path: str
    action_required: str


def expected_result_path(incoming_root: Path, batch_key: str) -> Path:
    return incoming_root.resolve() / f"{batch_key}.result.csv"


def outgoing_path(outgoing_root: Path, batch_key: str) -> Path:
    return outgoing_root.resolve() / f"{batch_key}.tasks.csv"


def _open_batch() -> dict[str, object] | None:
    ensure_qcc_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT batch_id, batch_key, status, task_count, export_path,
                       export_sha256, result_path, planned_at, exported_at
                FROM acquisition.cn_qcc_batch
                WHERE status IN ('PLANNED', 'EXPORTED', 'RESULT_RECEIVED')
                ORDER BY planned_at DESC
                LIMIT 1
                """
            )
            return cur.fetchone()


def _complete_empty_batch(batch_id: str) -> None:
    """Close a zero-task planner batch and advance only its durable scan state."""
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT batch_id, status, task_count, source_rank_to,
                       source_entity_to, backfill_bucket
                FROM acquisition.cn_qcc_batch
                WHERE batch_id = %s
                FOR UPDATE
                """,
                (batch_id,),
            )
            batch = cur.fetchone()
            if not batch:
                raise ValueError(f"unknown CN QCC batch: {batch_id}")
            if batch["status"] == "COMPLETED":
                return
            if batch["status"] != "PLANNED" or int(batch["task_count"] or 0) != 0:
                raise ValueError("only a zero-task PLANNED QCC batch can be auto-completed")
            cur.execute(
                """
                UPDATE acquisition.cn_qcc_batch
                SET status = 'COMPLETED', completed_at = now(),
                    metrics = metrics || '{"empty_batch": true}'::jsonb
                WHERE batch_id = %s
                """,
                (batch_id,),
            )
            cur.execute(
                """
                UPDATE acquisition.cn_qcc_planner_state
                SET source_rank_watermark = %s,
                    source_entity_watermark = %s,
                    backfill_bucket = %s,
                    last_completed_batch_id = %s,
                    updated_at = now()
                WHERE state_key = 'CN_QCC_APPLICANT'
                """,
                (
                    int(batch["source_rank_to"]),
                    str(batch["source_entity_to"] or ""),
                    (int(batch["backfill_bucket"]) + 1) % 52,
                    batch["batch_id"],
                ),
            )
        conn.commit()


def acquisition_state(
    *,
    enabled: bool,
    incoming_root: Path,
) -> AcquisitionState:
    if not enabled:
        return AcquisitionState(
            enabled=False,
            readiness="DISABLED",
            open_batch_id="",
            batch_key="",
            batch_status="",
            task_count=0,
            export_path="",
            result_expected_path="",
            action_required="enable CN_QCC_ACQUISITION_ENABLED to run acquisition cycles",
        )

    batch = _open_batch()
    if not batch:
        return AcquisitionState(
            enabled=True,
            readiness="READY_TO_PLAN",
            open_batch_id="",
            batch_key="",
            batch_status="",
            task_count=0,
            export_path="",
            result_expected_path="",
            action_required="run cycle to plan and export the next bounded batch",
        )

    batch_id = str(batch["batch_id"])
    batch_key = str(batch["batch_key"])
    status = str(batch["status"])
    result_path = expected_result_path(incoming_root, batch_key)
    if status == "PLANNED":
        readiness = "READY_TO_EXPORT"
        action = "run cycle to export the planned batch"
    elif result_path.is_file():
        readiness = "RESULT_READY"
        action = "run cycle to ingest the returned result CSV"
    else:
        readiness = "WAITING_RESULT"
        action = f"place collector result at {result_path}"
    return AcquisitionState(
        enabled=True,
        readiness=readiness,
        open_batch_id=batch_id,
        batch_key=batch_key,
        batch_status=status,
        task_count=int(batch["task_count"] or 0),
        export_path=str(batch.get("export_path") or ""),
        result_expected_path=str(result_path),
        action_required=action,
    )


def run_cycle(
    *,
    enabled: bool,
    capacity: int,
    refresh_days: int,
    outgoing_root: Path,
    incoming_root: Path,
) -> dict[str, object]:
    """Run one idempotent operational QCC acquisition cycle.

    A cycle performs at most one external-boundary transition: create+export a
    new bounded batch, export an existing planned batch, ingest one returned
    result, or report that the operator is waiting for the collector result.
    This makes the command safe for host schedulers to invoke repeatedly.
    """
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    if refresh_days <= 0:
        raise ValueError("refresh_days must be positive")

    before = acquisition_state(enabled=enabled, incoming_root=incoming_root)
    if not enabled:
        return {"action": "DISABLED", "state": asdict(before)}

    if before.readiness == "READY_TO_PLAN":
        plan = create_batch(capacity=capacity, refresh_days=refresh_days)
        if plan.task_count == 0:
            _complete_empty_batch(plan.batch_id)
            return {
                "action": "IDLE",
                "plan": plan_as_dict(plan),
                "state": asdict(acquisition_state(enabled=True, incoming_root=incoming_root)),
            }
        exported = export_batch(plan.batch_id, outgoing_path(outgoing_root, plan.batch_key))
        return {
            "action": "PLANNED_AND_EXPORTED",
            "plan": plan_as_dict(plan),
            "export": exported,
            "state": asdict(acquisition_state(enabled=True, incoming_root=incoming_root)),
        }

    if before.readiness == "READY_TO_EXPORT":
        if before.task_count == 0:
            _complete_empty_batch(before.open_batch_id)
            return {
                "action": "IDLE",
                "state": asdict(acquisition_state(enabled=True, incoming_root=incoming_root)),
            }
        exported = export_batch(
            before.open_batch_id,
            outgoing_path(outgoing_root, before.batch_key),
        )
        return {
            "action": "EXPORTED",
            "export": exported,
            "state": asdict(acquisition_state(enabled=True, incoming_root=incoming_root)),
        }

    if before.readiness == "RESULT_READY":
        result = ingest_result(before.open_batch_id, Path(before.result_expected_path))
        return {
            "action": "INGESTED",
            "result": result,
            "state": asdict(acquisition_state(enabled=True, incoming_root=incoming_root)),
        }

    return {"action": "WAITING_RESULT", "state": asdict(before)}


__all__ = [
    "AcquisitionState",
    "acquisition_state",
    "expected_result_path",
    "outgoing_path",
    "run_cycle",
]
