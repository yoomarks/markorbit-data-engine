from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db import postgres_conn
from app.us_tsdr.migrations import ensure_tsdr_schema

_ALLOWED_RESULTS = {"SUCCESS", "NOT_FOUND", "FAILED", "UNATTEMPTED"}


def _parse_timestamp(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_hash(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError("snapshot_hash must be a 64-character hex SHA-256")
    return text


def _validated_raw_file(package_dir: Path, value: object, expected_hash: str | None) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("SUCCESS result requires raw_relative_path")
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"raw_relative_path must stay inside the result package: {text}")
    path = (package_dir / relative).resolve()
    try:
        path.relative_to(package_dir)
    except ValueError as exc:
        raise ValueError(f"raw_relative_path escapes result package: {text}") from exc
    if not path.is_file():
        raise ValueError(f"raw result file is missing: {text}")
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if expected_hash and digest != expected_hash:
        raise ValueError(f"raw result SHA-256 mismatch for {text}")
    return relative.as_posix()


def _iter_results(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError(f"result line {line_number} is not an object")
            yield line_number, payload


def ingest_result_package(package_dir: Path) -> dict[str, object]:
    """Reconcile an external collector result package against exported task IDs."""
    ensure_tsdr_schema()
    package_dir = package_dir.resolve()
    manifest_path = package_dir / "manifest.json"
    result_path = package_dir / "results.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract") != "US_TSDR_RESULT_V1":
        raise ValueError("unsupported TSDR result contract")
    batch_key = str(manifest.get("batch_key") or "").strip()
    if not batch_key:
        raise ValueError("result manifest missing batch_key")
    if not result_path.is_file():
        raise ValueError("result package missing results.jsonl")

    result_rows: list[tuple[Any, ...]] = []
    seen_task_ids: set[str] = set()
    counts = {status: 0 for status in sorted(_ALLOWED_RESULTS)}

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT batch_id, batch_key, status, source_rank_to, source_serial_to
                FROM acquisition.us_tsdr_batch
                WHERE batch_key = %s
                FOR UPDATE
                """,
                (batch_key,),
            )
            batch = cur.fetchone()
            if batch is None:
                raise ValueError(f"result references unknown batch: {batch_key}")
            if batch["status"] not in {"EXPORTED", "RESULT_RECEIVED"}:
                raise ValueError(f"batch {batch_key} cannot ingest results from {batch['status']}")

            cur.execute(
                """
                SELECT task_id, serial_number, task_type, lifecycle_state,
                       source_attorney_fingerprint, source_attorney_present
                FROM acquisition.us_tsdr_task
                WHERE batch_id = %s
                """,
                (batch["batch_id"],),
            )
            expected = {str(row["task_id"]): dict(row) for row in cur.fetchall()}

            for line_number, payload in _iter_results(result_path):
                task_id = str(payload.get("task_id") or "")
                if not task_id or task_id in seen_task_ids:
                    raise ValueError(f"duplicate/missing task_id on result line {line_number}")
                seen_task_ids.add(task_id)
                task = expected.get(task_id)
                if task is None:
                    raise ValueError(f"unknown task_id on result line {line_number}: {task_id}")
                serial = str(payload.get("serial_number") or "").strip()
                if serial != task["serial_number"]:
                    raise ValueError(f"serial_number mismatch for task {task_id}")
                status = str(payload.get("result_status") or "").strip().upper()
                if status not in _ALLOWED_RESULTS:
                    raise ValueError(f"unsupported result_status for task {task_id}: {status}")
                fetched_at = _parse_timestamp(payload.get("fetched_at"))
                snapshot_hash = _validate_hash(payload.get("snapshot_hash"))
                if status == "SUCCESS" and (fetched_at is None or snapshot_hash is None):
                    raise ValueError(f"SUCCESS task {task_id} requires fetched_at and snapshot_hash")
                if status == "SUCCESS":
                    raw_relative_path = _validated_raw_file(
                        package_dir, payload.get("raw_relative_path"), snapshot_hash
                    )
                else:
                    raw_relative_path = str(payload.get("raw_relative_path") or "").strip()
                error_message = str(payload.get("error_message") or "").strip()
                result_rows.append(
                    (
                        status,
                        status,
                        fetched_at,
                        snapshot_hash,
                        raw_relative_path or None,
                        error_message or None,
                        task_id,
                    )
                )
                counts[status] += 1

            missing_ids = sorted(set(expected) - seen_task_ids)

            for offset in range(0, len(result_rows), 1_000):
                cur.executemany(
                    """
                    UPDATE acquisition.us_tsdr_task
                    SET state = %s, result_status = %s, fetched_at = %s,
                        snapshot_hash = %s, raw_relative_path = %s,
                        error_message = %s, completed_at = now()
                    WHERE task_id = %s
                    """,
                    result_rows[offset : offset + 1_000],
                )
            if missing_ids:
                cur.execute(
                    """
                    UPDATE acquisition.us_tsdr_task
                    SET state = 'UNATTEMPTED', result_status = 'UNATTEMPTED',
                        error_message = 'Collector result package omitted this exported task.',
                        completed_at = now()
                    WHERE task_id = ANY(%s::uuid[])
                    """,
                    ([uuid.UUID(value) for value in missing_ids],),
                )
                counts["UNATTEMPTED"] += len(missing_ids)

            cur.execute(
                """
                INSERT INTO acquisition.us_tsdr_case_coverage (
                    serial_number, first_fetched_at, last_fetched_at, last_result_status,
                    last_snapshot_hash, last_source_attorney_fingerprint,
                    last_source_attorney_present, last_changed_at, lifecycle_state,
                    terminal_complete, last_batch_id, last_task_type,
                    successful_fetch_count, updated_at
                )
                SELECT
                    t.serial_number,
                    CASE WHEN t.state = 'SUCCESS' THEN t.fetched_at ELSE NULL END,
                    CASE WHEN t.state = 'SUCCESS' THEN t.fetched_at ELSE NULL END,
                    COALESCE(t.result_status, t.state),
                    CASE WHEN t.state = 'SUCCESS' THEN t.snapshot_hash ELSE NULL END,
                    t.source_attorney_fingerprint,
                    t.source_attorney_present,
                    CASE WHEN t.state = 'SUCCESS' THEN t.fetched_at ELSE NULL END,
                    t.lifecycle_state,
                    (t.state = 'SUCCESS' AND t.task_type IN ('FINAL_FETCH', 'TERMINAL_INITIAL_FETCH')),
                    t.batch_id,
                    t.task_type,
                    CASE WHEN t.state = 'SUCCESS' THEN 1 ELSE 0 END,
                    now()
                FROM acquisition.us_tsdr_task t
                WHERE t.batch_id = %s
                ON CONFLICT (serial_number) DO UPDATE SET
                    first_fetched_at = COALESCE(acquisition.us_tsdr_case_coverage.first_fetched_at, EXCLUDED.first_fetched_at),
                    last_fetched_at = COALESCE(EXCLUDED.last_fetched_at, acquisition.us_tsdr_case_coverage.last_fetched_at),
                    last_result_status = EXCLUDED.last_result_status,
                    last_snapshot_hash = COALESCE(EXCLUDED.last_snapshot_hash, acquisition.us_tsdr_case_coverage.last_snapshot_hash),
                    last_source_attorney_fingerprint = COALESCE(EXCLUDED.last_source_attorney_fingerprint, acquisition.us_tsdr_case_coverage.last_source_attorney_fingerprint),
                    last_source_attorney_present = EXCLUDED.last_source_attorney_present,
                    last_changed_at = CASE
                        WHEN EXCLUDED.last_snapshot_hash IS NOT NULL
                         AND EXCLUDED.last_snapshot_hash IS DISTINCT FROM acquisition.us_tsdr_case_coverage.last_snapshot_hash
                        THEN EXCLUDED.last_fetched_at
                        ELSE acquisition.us_tsdr_case_coverage.last_changed_at
                    END,
                    lifecycle_state = EXCLUDED.lifecycle_state,
                    terminal_complete = acquisition.us_tsdr_case_coverage.terminal_complete OR EXCLUDED.terminal_complete,
                    last_batch_id = EXCLUDED.last_batch_id,
                    last_task_type = EXCLUDED.last_task_type,
                    successful_fetch_count = acquisition.us_tsdr_case_coverage.successful_fetch_count + EXCLUDED.successful_fetch_count,
                    updated_at = now()
                """,
                (batch["batch_id"],),
            )

            total = len(expected)
            gaps = counts["FAILED"] + counts["UNATTEMPTED"]
            metrics = {
                "result_counts": counts,
                "expected_task_count": total,
                "completed_with_gaps": gaps > 0,
            }
            cur.execute(
                """
                UPDATE acquisition.us_tsdr_batch
                SET status = 'COMPLETED', result_received_at = now(), completed_at = now(),
                    result_path = %s, metrics = metrics || %s::jsonb
                WHERE batch_id = %s
                """,
                (str(package_dir), json.dumps(metrics), batch["batch_id"]),
            )
            cur.execute(
                """
                UPDATE acquisition.us_tsdr_planner_state
                SET source_rank_watermark = %s,
                    source_serial_watermark = %s,
                    last_completed_batch_id = %s,
                    updated_at = now()
                WHERE state_key = 'US_TSDR_WEEKLY'
                """,
                (
                    int(batch["source_rank_to"]),
                    batch["source_serial_to"],
                    batch["batch_id"],
                ),
            )
        conn.commit()

    return {
        "batch_key": batch_key,
        "status": "COMPLETED",
        "task_count": len(expected),
        "result_counts": counts,
        "completed_with_gaps": counts["FAILED"] + counts["UNATTEMPTED"] > 0,
    }
