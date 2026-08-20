from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.db import postgres_conn
from app.us_tsdr.collector_contract import CollectorObservation, parse_collector_csv
from app.us_tsdr.migrations import ensure_tsdr_schema


COLLECTOR_OBSERVATION_SCHEMA_VERSION = "US_TSDR_COLLECTOR_OBSERVATION_V1"

_OBSERVATION_SQL = r'''
CREATE TABLE IF NOT EXISTS acquisition.us_tsdr_contact_observation (
    observation_id uuid PRIMARY KEY,
    task_id uuid NOT NULL UNIQUE
        REFERENCES acquisition.us_tsdr_task(task_id) ON DELETE RESTRICT,
    batch_id uuid NOT NULL
        REFERENCES acquisition.us_tsdr_batch(batch_id) ON DELETE RESTRICT,
    serial_number text NOT NULL,
    source_csv_path text NOT NULL,
    source_csv_sha256 char(64) NOT NULL,
    observation_sha256 char(64) NOT NULL,
    source_url text NOT NULL DEFAULT '',
    collected_at timestamptz,
    collected_at_evidence text NOT NULL,
    attorney_name text NOT NULL DEFAULT '',
    docket_number text NOT NULL DEFAULT '',
    attorney_primary_email text NOT NULL DEFAULT '',
    attorney_email_authorized boolean,
    correspondent_name_address_raw text NOT NULL DEFAULT '',
    correspondent_name_address_lines text[] NOT NULL DEFAULT ARRAY[]::text[],
    phone text NOT NULL DEFAULT '',
    correspondent_emails text[] NOT NULL DEFAULT ARRAY[]::text[],
    correspondent_email_authorized boolean,
    raw_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CHECK (serial_number ~ '^[0-9]{8}$'),
    CHECK (source_csv_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CHECK (observation_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CHECK (collected_at_evidence IN ('COLLECTOR_FIELD', 'INGESTED_AT_FALLBACK'))
);

CREATE INDEX IF NOT EXISTS ix_us_tsdr_contact_observation_serial
ON acquisition.us_tsdr_contact_observation (serial_number, ingested_at DESC);
'''


def ensure_collector_observation_schema() -> None:
    ensure_tsdr_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_OBSERVATION_SQL)
        conn.commit()


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _observation_sha256(observation: CollectorObservation) -> str:
    payload = json.dumps(
        observation.normalized_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_csv_observations(result_dir: Path) -> list[tuple[Path, CollectorObservation]]:
    rows: list[tuple[Path, CollectorObservation]] = []
    for path in sorted(result_dir.rglob("*.csv")):
        for observation in parse_collector_csv(path):
            rows.append((path, observation))
    return rows


def ingest_collector_csv_directory(
    batch_key: str,
    result_dir: Path,
    *,
    ingested_at: datetime | None = None,
) -> dict[str, object]:
    """Ingest a returned collector CSV directory and close the weekly batch.

    CSV rows are reconciled by serial number against the durable exported task
    ledger. A collector CSV does not need task IDs. Any exported task without a
    returned CSV observation is explicitly marked UNATTEMPTED so it can be ranked
    again in a later weekly batch.
    """
    ensure_collector_observation_schema()
    result_dir = Path(result_dir).resolve()
    if not result_dir.is_dir():
        raise ValueError(f"TSDR collector result directory does not exist: {result_dir}")
    received_at = ingested_at or datetime.now(timezone.utc)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    received_at = received_at.astimezone(timezone.utc)

    parsed_rows = _load_csv_observations(result_dir)
    if not parsed_rows:
        raise ValueError(f"no collector CSV observations found under {result_dir}")

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
                raise ValueError(f"unknown TSDR batch: {batch_key}")
            if batch["status"] not in {"EXPORTED", "RESULT_RECEIVED"}:
                raise ValueError(
                    f"batch {batch_key} cannot ingest collector CSV from {batch['status']}"
                )

            cur.execute(
                """
                SELECT task_id, serial_number, task_type, lifecycle_state,
                       source_attorney_fingerprint, source_attorney_present
                FROM acquisition.us_tsdr_task
                WHERE batch_id = %s
                """,
                (batch["batch_id"],),
            )
            tasks = cur.fetchall()
            expected = {str(row["serial_number"]): dict(row) for row in tasks}

            seen_serials: set[str] = set()
            success_rows: list[tuple[object, ...]] = []
            observation_rows: list[tuple[object, ...]] = []
            for source_path, observation in parsed_rows:
                serial = observation.serial_number
                if serial in seen_serials:
                    raise ValueError(f"duplicate collector observation for serial {serial}")
                seen_serials.add(serial)
                task = expected.get(serial)
                if task is None:
                    raise ValueError(
                        f"collector CSV returned serial {serial} that is not in batch {batch_key}"
                    )

                source_hash = _file_sha256(source_path)
                observation_hash = _observation_sha256(observation)
                collected_at = observation.collected_at or received_at
                collected_at_evidence = (
                    "COLLECTOR_FIELD"
                    if observation.collected_at is not None
                    else "INGESTED_AT_FALLBACK"
                )
                try:
                    relative_path = source_path.relative_to(result_dir).as_posix()
                except ValueError:
                    relative_path = source_path.name

                observation_rows.append(
                    (
                        uuid.uuid4(),
                        task["task_id"],
                        batch["batch_id"],
                        serial,
                        relative_path,
                        source_hash,
                        observation_hash,
                        observation.source_url,
                        collected_at,
                        collected_at_evidence,
                        observation.attorney_name,
                        observation.docket_number,
                        observation.attorney_primary_email,
                        observation.attorney_email_authorized,
                        observation.correspondent_name_address_raw,
                        list(observation.correspondent_name_address_lines),
                        observation.phone,
                        list(observation.correspondent_emails),
                        observation.correspondent_email_authorized,
                        json.dumps(observation.raw_fields, ensure_ascii=False),
                    )
                )
                success_rows.append(
                    (
                        collected_at,
                        observation_hash,
                        relative_path,
                        task["task_id"],
                    )
                )

            for offset in range(0, len(observation_rows), 1_000):
                cur.executemany(
                    """
                    INSERT INTO acquisition.us_tsdr_contact_observation (
                        observation_id, task_id, batch_id, serial_number,
                        source_csv_path, source_csv_sha256, observation_sha256,
                        source_url, collected_at, collected_at_evidence,
                        attorney_name, docket_number, attorney_primary_email,
                        attorney_email_authorized, correspondent_name_address_raw,
                        correspondent_name_address_lines, phone, correspondent_emails,
                        correspondent_email_authorized, raw_fields
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                    """,
                    observation_rows[offset : offset + 1_000],
                )

            for offset in range(0, len(success_rows), 1_000):
                cur.executemany(
                    """
                    UPDATE acquisition.us_tsdr_task
                    SET state = 'SUCCESS', result_status = 'SUCCESS', fetched_at = %s,
                        snapshot_hash = %s, raw_relative_path = %s,
                        error_message = NULL, completed_at = now()
                    WHERE task_id = %s
                    """,
                    success_rows[offset : offset + 1_000],
                )

            missing_serials = sorted(set(expected) - seen_serials)
            if missing_serials:
                cur.execute(
                    """
                    UPDATE acquisition.us_tsdr_task
                    SET state = 'UNATTEMPTED', result_status = 'UNATTEMPTED',
                        error_message = 'Collector CSV directory omitted this exported task.',
                        completed_at = now()
                    WHERE batch_id = %s AND serial_number = ANY(%s)
                    """,
                    (batch["batch_id"], missing_serials),
                )

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

            metrics = {
                "collector_contract": "US_TSDR_COLLECTOR_TXT_CSV_V1",
                "collector_csv_success": len(seen_serials),
                "collector_csv_unattempted": len(missing_serials),
                "collector_csv_files": len({str(path) for path, _ in parsed_rows}),
            }
            cur.execute(
                """
                UPDATE acquisition.us_tsdr_batch
                SET status = 'COMPLETED', result_received_at = now(), completed_at = now(),
                    result_path = %s, metrics = metrics || %s::jsonb
                WHERE batch_id = %s
                """,
                (str(result_dir), json.dumps(metrics), batch["batch_id"]),
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
        "success": len(seen_serials),
        "unattempted": len(missing_serials),
        "result_dir": str(result_dir),
    }
