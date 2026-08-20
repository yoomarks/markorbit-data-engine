from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import uuid

from app.cn_qcc import POLICY_VERSION
from app.cn_qcc.migrations import ensure_qcc_schema
from app.cn_qcc.policy import QccCandidate, PlannedCandidate, select_candidates
from app.cn_qcc.source_candidates import CandidatePool, load_candidate_pool
from app.db import postgres_conn


_STATE_KEY = "CN_QCC_APPLICANT"
_LOCK_KEY = 817_240_311


@dataclass(frozen=True)
class BatchPlan:
    batch_id: str
    batch_key: str
    policy_version: str
    status: str
    target_capacity: int
    refresh_days: int
    backfill_bucket: int
    task_count: int
    source_watermark_from: tuple[int, str]
    source_watermark_to: tuple[int, str]
    lane_counts: dict[str, int]


def _planner_state() -> tuple[tuple[int, str], int]:
    ensure_qcc_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_rank_watermark, source_entity_watermark, backfill_bucket
                FROM acquisition.cn_qcc_planner_state
                WHERE state_key = %s
                """,
                (_STATE_KEY,),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError("CN QCC planner state is missing")
    return (
        (int(row["source_rank_watermark"]), str(row["source_entity_watermark"] or "")),
        int(row["backfill_bucket"]),
    )


def _existing_open_batch() -> dict[str, object] | None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT batch_id, batch_key, policy_version, status, target_capacity,
                       refresh_days, backfill_bucket, task_count,
                       source_rank_from, source_entity_from,
                       source_rank_to, source_entity_to, metrics
                FROM acquisition.cn_qcc_batch
                WHERE status IN ('PLANNED', 'EXPORTED', 'RESULT_RECEIVED')
                ORDER BY planned_at DESC
                LIMIT 1
                """
            )
            return cur.fetchone()


def _row_to_plan(row: dict[str, object]) -> BatchPlan:
    metrics = row.get("metrics") or {}
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    return BatchPlan(
        batch_id=str(row["batch_id"]),
        batch_key=str(row["batch_key"]),
        policy_version=str(row["policy_version"]),
        status=str(row["status"]),
        target_capacity=int(row["target_capacity"]),
        refresh_days=int(row["refresh_days"]),
        backfill_bucket=int(row["backfill_bucket"]),
        task_count=int(row["task_count"]),
        source_watermark_from=(int(row["source_rank_from"]), str(row["source_entity_from"] or "")),
        source_watermark_to=(int(row["source_rank_to"]), str(row["source_entity_to"] or "")),
        lane_counts=dict(metrics.get("lane_counts") or {}),
    )


def _persist_batch(
    *,
    selected: list[PlannedCandidate],
    capacity: int,
    refresh_days: int,
    source_watermark_from: tuple[int, str],
    pool: CandidatePool,
) -> BatchPlan:
    batch_id = uuid.uuid4()
    batch_key = f"CN_QCC_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{str(batch_id)[:8]}"
    metrics = {"lane_counts": pool.lane_counts, "selected": len(selected)}
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            cur.execute(
                """
                SELECT batch_id FROM acquisition.cn_qcc_batch
                WHERE status IN ('PLANNED', 'EXPORTED', 'RESULT_RECEIVED')
                LIMIT 1
                """
            )
            if cur.fetchone():
                raise RuntimeError("CN QCC has an open batch; complete it before planning another")
            cur.execute(
                """
                INSERT INTO acquisition.cn_qcc_batch (
                    batch_id, batch_key, policy_version, status, target_capacity,
                    refresh_days, backfill_bucket, task_count,
                    source_rank_from, source_entity_from, source_rank_to, source_entity_to,
                    metrics
                ) VALUES (%s, %s, %s, 'PLANNED', %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    batch_id,
                    batch_key,
                    POLICY_VERSION,
                    capacity,
                    refresh_days,
                    pool.backfill_bucket,
                    len(selected),
                    source_watermark_from[0],
                    source_watermark_from[1],
                    pool.source_watermark_to[0],
                    pool.source_watermark_to[1],
                    json.dumps(metrics, ensure_ascii=False),
                ),
            )
            for item in selected:
                candidate = item.candidate
                cur.execute(
                    """
                    INSERT INTO acquisition.cn_qcc_task (
                        task_id, batch_id, entity_id, task_type, priority_score,
                        reason_codes, applicant_name, normalized_name, applicant_address,
                        country_code, region_code, city, trademark_count,
                        latest_application_number, source_rank, source_fingerprint
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        uuid.uuid4(),
                        batch_id,
                        candidate.entity_id,
                        item.task_type,
                        item.priority_score,
                        list(item.reason_codes),
                        candidate.applicant_name,
                        candidate.normalized_name,
                        candidate.applicant_address,
                        candidate.country_code,
                        candidate.region_code,
                        candidate.city,
                        candidate.trademark_count,
                        candidate.latest_application_number,
                        candidate.source_rank,
                        candidate.source_fingerprint,
                    ),
                )
        conn.commit()
    return BatchPlan(
        batch_id=str(batch_id),
        batch_key=batch_key,
        policy_version=POLICY_VERSION,
        status="PLANNED",
        target_capacity=capacity,
        refresh_days=refresh_days,
        backfill_bucket=pool.backfill_bucket,
        task_count=len(selected),
        source_watermark_from=source_watermark_from,
        source_watermark_to=pool.source_watermark_to,
        lane_counts=pool.lane_counts,
    )


def create_batch(*, capacity: int, refresh_days: int = 180) -> BatchPlan:
    """Create one bounded QCC enrichment batch from current CN applicant facts.

    The caller controls capacity and refresh cadence. Only one open batch is
    allowed, so periodic invocations are safe and cannot create duplicate work.
    """
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    if refresh_days <= 0:
        raise ValueError("refresh_days must be positive")
    watermark, backfill_bucket = _planner_state()
    existing = _existing_open_batch()
    if existing:
        return _row_to_plan(existing)
    pool = load_candidate_pool(
        source_watermark=watermark,
        capacity=capacity,
        backfill_bucket=backfill_bucket,
    )
    selected = select_candidates(pool.candidates, capacity=capacity)
    return _persist_batch(
        selected=selected,
        capacity=capacity,
        refresh_days=refresh_days,
        source_watermark_from=watermark,
        pool=pool,
    )


def create_batch_from_candidates(
    candidates: list[QccCandidate],
    *,
    capacity: int,
    refresh_days: int = 180,
    source_watermark_from: tuple[int, str] = (0, ""),
    source_watermark_to: tuple[int, str] = (0, ""),
    backfill_bucket: int = 0,
) -> BatchPlan:
    """Deterministic fixture/operator hook; production planning uses create_batch()."""
    ensure_qcc_schema()
    pool = CandidatePool(
        candidates=candidates,
        source_watermark_to=source_watermark_to,
        backfill_bucket=backfill_bucket % 52,
        lane_counts={"supplied_candidates": len(candidates)},
    )
    selected = select_candidates(candidates, capacity=capacity)
    return _persist_batch(
        selected=selected,
        capacity=capacity,
        refresh_days=refresh_days,
        source_watermark_from=source_watermark_from,
        pool=pool,
    )


def plan_as_dict(plan: BatchPlan) -> dict[str, object]:
    payload = asdict(plan)
    payload["source_watermark_from"] = list(plan.source_watermark_from)
    payload["source_watermark_to"] = list(plan.source_watermark_to)
    return payload


__all__ = ["BatchPlan", "create_batch", "create_batch_from_candidates", "plan_as_dict"]
