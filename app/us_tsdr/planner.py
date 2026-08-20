from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Iterable

from app.db import postgres_conn
from app.us_tsdr.migrations import ensure_tsdr_schema
from app.us_tsdr.policy import DEFAULT_WEEKLY_CAPACITY, POLICY_VERSION, Candidate, select_weekly_batch

_STATE_KEY = "US_TSDR_WEEKLY"
_OPEN_BATCH_STATUSES = ("PLANNED", "EXPORTED", "RESULT_RECEIVED")


class OpenBatchError(RuntimeError):
    pass


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def planner_state() -> dict[str, object]:
    ensure_tsdr_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT state_key, source_rank_watermark, source_serial_watermark,
                       last_completed_batch_id, updated_at
                FROM acquisition.us_tsdr_planner_state
                WHERE state_key = %s
                """,
                (_STATE_KEY,),
            )
            row = cur.fetchone()
    if row is None:
        raise RuntimeError("US TSDR planner state is missing")
    return dict(row)


def _load_coverage(serial_numbers: list[str]) -> dict[str, dict[str, object]]:
    if not serial_numbers:
        return {}
    rows: dict[str, dict[str, object]] = {}
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for offset in range(0, len(serial_numbers), 10_000):
                chunk = serial_numbers[offset : offset + 10_000]
                cur.execute(
                    """
                    SELECT serial_number, first_fetched_at, last_fetched_at,
                           last_result_status, refresh_due_at, lifecycle_state,
                           terminal_complete, last_source_attorney_fingerprint,
                           last_source_attorney_present
                    FROM acquisition.us_tsdr_case_coverage
                    WHERE serial_number = ANY(%s)
                    """,
                    (chunk,),
                )
                for row in cur.fetchall():
                    rows[str(row["serial_number"])] = dict(row)
    return rows


def _overlay_coverage(candidates: Iterable[Candidate]) -> list[Candidate]:
    base = list(candidates)
    coverage = _load_coverage([item.serial_number for item in base])
    result: list[Candidate] = []
    for item in base:
        row = coverage.get(item.serial_number)
        if row is None:
            result.append(item)
            continue
        last_fetched_at = row.get("last_fetched_at")
        previous_fingerprint = str(row.get("last_source_attorney_fingerprint") or "")
        fingerprint_changed = bool(
            previous_fingerprint
            and item.source_attorney_fingerprint
            and previous_fingerprint != item.source_attorney_fingerprint
        )
        previous_present = row.get("last_source_attorney_present")
        result.append(
            replace(
                item,
                never_fetched=last_fetched_at is None,
                terminal_complete=bool(row.get("terminal_complete")),
                refresh_due_at=row.get("refresh_due_at"),
                last_fetched_at=last_fetched_at,
                representation_changed=item.representation_changed or fingerprint_changed,
                attorney_removed=item.attorney_removed or bool(
                    fingerprint_changed and previous_present is True and not item.current_attorney_present
                ),
                retry_required=str(row.get("last_result_status") or "").upper()
                in {"FAILED", "UNATTEMPTED"},
            )
        )
    return result


def _assert_no_open_batch(cur) -> None:
    cur.execute(
        """
        SELECT batch_id, batch_key, status, planned_at
        FROM acquisition.us_tsdr_batch
        WHERE status = ANY(%s)
        ORDER BY planned_at
        LIMIT 1
        """,
        (list(_OPEN_BATCH_STATUSES),),
    )
    row = cur.fetchone()
    if row:
        raise OpenBatchError(
            f"TSDR batch {row['batch_key']} is still {row['status']}; "
            "ingest/close it before planning another weekly batch"
        )


def _batch_key(now: datetime) -> str:
    iso = now.isocalendar()
    return f"TSDR-W{iso.year}-{iso.week:02d}-{now:%Y%m%d%H%M%S}"


def create_weekly_batch(
    candidates: Iterable[Candidate],
    *,
    capacity: int = DEFAULT_WEEKLY_CAPACITY,
    now: datetime | None = None,
    backfill_bucket: int = -1,
    source_watermark_to: tuple[int, str] | None = None,
) -> dict[str, object]:
    """Persist one weekly TSDR batch from a pre-bounded candidate pool."""
    ensure_tsdr_schema()
    now = now or datetime.now(timezone.utc)
    state = planner_state()
    watermark_rank = int(state["source_rank_watermark"] or 0)
    watermark_serial = str(state["source_serial_watermark"] or "")

    enriched = _overlay_coverage(candidates)
    selected = select_weekly_batch(enriched, capacity=capacity, now=now)

    batch_id = uuid.uuid4()
    batch_key = _batch_key(now)
    new_items = [item for item in selected if item.hard_new_application]
    watermark_to_rank, watermark_to_serial = source_watermark_to or (
        watermark_rank,
        watermark_serial,
    )

    reason_counts: dict[str, int] = {}
    task_type_counts: dict[str, int] = {}
    for item in selected:
        task_type_counts[item.task_type] = task_type_counts.get(item.task_type, 0) + 1
        for reason in item.reason_codes:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    metrics = {
        "selected": len(selected),
        "capacity": capacity,
        "new_application_count": len(new_items),
        "reason_counts": reason_counts,
        "task_type_counts": task_type_counts,
    }

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("markorbit:us-tsdr-weekly-plan",),
            )
            _assert_no_open_batch(cur)
            cur.execute(
                """
                INSERT INTO acquisition.us_tsdr_batch (
                    batch_id, batch_key, policy_version, backfill_bucket, status,
                    target_capacity, task_count, source_rank_from, source_serial_from,
                    source_rank_to, source_serial_to, planned_at, metrics
                ) VALUES (%s, %s, %s, %s, 'PLANNED', %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    batch_id,
                    batch_key,
                    POLICY_VERSION,
                    backfill_bucket,
                    capacity,
                    len(selected),
                    watermark_rank,
                    watermark_serial,
                    watermark_to_rank,
                    watermark_to_serial,
                    now,
                    _json(metrics),
                ),
            )
            rows = [
                (
                    uuid.uuid4(),
                    batch_id,
                    item.candidate.serial_number,
                    item.task_type,
                    item.priority_score,
                    list(item.reason_codes),
                    item.candidate.applicant_country,
                    int(item.candidate.source_rank),
                    item.candidate.lifecycle_state,
                    item.candidate.source_attorney_fingerprint or None,
                    item.candidate.current_attorney_present,
                )
                for item in selected
            ]
            if rows:
                cur.executemany(
                    """
                    INSERT INTO acquisition.us_tsdr_task (
                        task_id, batch_id, serial_number, task_type, priority_score,
                        reason_codes, applicant_country, source_rank, lifecycle_state,
                        source_attorney_fingerprint, source_attorney_present
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
        conn.commit()

    return {
        "batch_id": str(batch_id),
        "batch_key": batch_key,
        "policy_version": POLICY_VERSION,
        "status": "PLANNED",
        "backfill_bucket": backfill_bucket,
        "task_count": len(selected),
        "capacity": capacity,
        "source_watermark_from": [watermark_rank, watermark_serial],
        "source_watermark_to": [watermark_to_rank, watermark_to_serial],
        "metrics": metrics,
    }
