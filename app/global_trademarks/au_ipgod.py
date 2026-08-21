from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Iterable

from app.db import postgres_conn
from app.global_trademarks.ingest_runs import (
    begin_or_resume_ingest_run,
    checkpoint_ingest_run,
    complete_ingest_run,
    fail_ingest_run,
)
from app.global_trademarks.ingest_schema import ensure_seed_ingest_schema
from app.global_trademarks.source_objects import register_source_object


def _date(value: str | None) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value[:10]) if value else None


def _bool(value: str | None) -> bool | None:
    value = (value or "").strip().lower()
    if not value:
        return None
    if value in {"true", "t", "1", "yes", "y"}:
        return True
    if value in {"false", "f", "0", "no", "n"}:
        return False
    raise ValueError(f"unsupported boolean value: {value}")


def _row_hash(table_name: str, row: dict[str, str]) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{table_name}\0{payload}".encode()).hexdigest()


def _iter_trade_mark_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if "ip_right_type" not in (reader.fieldnames or []):
            raise ValueError(f"IPGOD file missing ip_right_type: {path}")
        for row in reader:
            if (row.get("ip_right_type") or "").strip().lower() == "trade_mark":
                yield row


def _batch_execute(
    sql: str,
    rows: Iterable[tuple],
    *,
    batch_size: int,
    max_records: int | None,
    source_object,
    pipeline_id: str,
    table_name: str,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_records is not None and max_records <= 0:
        raise ValueError("max_records must be positive when provided")

    run = begin_or_resume_ingest_run(
        source_object_id=source_object,
        jurisdiction="AU",
        pipeline_id=pipeline_id,
        metadata={
            "ipgod_table": table_name,
            "batch_size": batch_size,
            "max_records": max_records,
        },
    )
    if run.complete:
        return run.rows_committed

    rows_committed = run.rows_committed
    invocation_committed = 0
    checkpoint = run.checkpoint
    record_position = 0
    bounded_stop = False
    batch: list[tuple] = []

    try:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    record_position += 1
                    if record_position <= checkpoint:
                        continue
                    batch.append(row)
                    limit_reached = bool(
                        max_records is not None
                        and invocation_committed + len(batch) >= max_records
                    )
                    if len(batch) >= batch_size or limit_reached:
                        cur.executemany(sql, batch)
                        rows_committed += len(batch)
                        invocation_committed += len(batch)
                        checkpoint = record_position
                        checkpoint_ingest_run(
                            cur,
                            run_id=run.run_id,
                            checkpoint=checkpoint,
                            rows_committed=rows_committed,
                        )
                        conn.commit()
                        batch.clear()
                        if limit_reached:
                            bounded_stop = True
                            break

                if batch:
                    cur.executemany(sql, batch)
                    rows_committed += len(batch)
                    invocation_committed += len(batch)
                    checkpoint = record_position

                if not bounded_stop:
                    complete_ingest_run(
                        cur,
                        run_id=run.run_id,
                        checkpoint=max(checkpoint, record_position),
                        rows_committed=rows_committed,
                    )
                conn.commit()
    except Exception as exc:
        fail_ingest_run(run_id=run.run_id, error_text=f"{type(exc).__name__}: {exc}")
        raise

    return rows_committed


def _source_object(path: Path, *, object_key: str | None, table_name: str):
    ensure_seed_ingest_schema()
    return register_source_object(
        jurisdiction="AU",
        source_id="IPGOD_2022",
        path=path,
        object_key=object_key,
        metadata={"ipgod_table": table_name},
    )


def ingest_application(
    path: Path,
    *,
    object_key: str | None = None,
    batch_size: int = 5000,
    max_records: int | None = None,
) -> int:
    table_name = "application"
    source_object = _source_object(path, object_key=object_key, table_name=table_name)
    sql = """
        INSERT INTO trademark_au.application (
            application_number, ip_right_sub_type, source_status, earliest_filed_date,
            priority_date, gained_registration_status_date, gained_enforceable_status_date,
            enforceable_from_date, deemed_retired_date, source_object_id, source_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (application_number) DO UPDATE SET
            ip_right_sub_type = EXCLUDED.ip_right_sub_type,
            source_status = EXCLUDED.source_status,
            earliest_filed_date = EXCLUDED.earliest_filed_date,
            priority_date = EXCLUDED.priority_date,
            gained_registration_status_date = EXCLUDED.gained_registration_status_date,
            gained_enforceable_status_date = EXCLUDED.gained_enforceable_status_date,
            enforceable_from_date = EXCLUDED.enforceable_from_date,
            deemed_retired_date = EXCLUDED.deemed_retired_date,
            source_object_id = EXCLUDED.source_object_id,
            source_payload = EXCLUDED.source_payload
    """

    def rows():
        for row in _iter_trade_mark_rows(path):
            application_number = (row.get("application_number") or "").strip()
            if not application_number:
                continue
            yield (
                application_number,
                (row.get("ip_right_sub_type") or "").strip() or None,
                (row.get("status") or "").strip() or None,
                _date(row.get("earliest_filed_date")),
                _date(row.get("priority_date")),
                _date(row.get("gained_registration_status_date")),
                _date(row.get("gained_enforceable_status_date")),
                _date(row.get("enforceable_from_date")),
                _date(row.get("deemed_retired_date")),
                source_object,
                json.dumps(row, ensure_ascii=False),
            )

    return _batch_execute(
        sql,
        rows(),
        batch_size=batch_size,
        max_records=max_records,
        source_object=source_object,
        pipeline_id="IPGOD_2022_APPLICATION_V1",
        table_name=table_name,
    )


def ingest_party_activity(
    path: Path,
    *,
    object_key: str | None = None,
    batch_size: int = 5000,
    max_records: int | None = None,
) -> int:
    table_name = "party-activity"
    source_object = _source_object(path, object_key=object_key, table_name=table_name)
    sql = """
        INSERT INTO trademark_au.party_activity (
            source_row_hash, application_number, party_id, party_role, party_role_category,
            party_type, party_name, abn, country_code, state_code, postcode,
            effective_from_date, effective_to_date, is_current, source_object_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_row_hash) DO NOTHING
    """

    def rows():
        for row in _iter_trade_mark_rows(path):
            application_number = (row.get("application_number") or "").strip()
            party_id = (row.get("party_id") or "").strip()
            party_role = (row.get("party_role") or "").strip()
            if not application_number or not party_id or not party_role:
                continue
            yield (
                _row_hash(table_name, row),
                application_number,
                int(party_id),
                party_role,
                (row.get("party_role_category") or "").strip() or None,
                (row.get("party_type") or "").strip() or None,
                (row.get("party_name") or "").strip() or None,
                (row.get("abn") or "").strip() or None,
                (row.get("country_code") or "").strip() or None,
                (row.get("state_code") or "").strip() or None,
                (row.get("postcode") or "").strip() or None,
                _date(row.get("effective_from_date")),
                _date(row.get("effective_to_date")),
                bool(_bool(row.get("is_current"))),
                source_object,
            )

    return _batch_execute(
        sql,
        rows(),
        batch_size=batch_size,
        max_records=max_records,
        source_object=source_object,
        pipeline_id="IPGOD_2022_PARTY_ACTIVITY_V1",
        table_name=table_name,
    )


def ingest_application_links(
    path: Path,
    *,
    object_key: str | None = None,
    batch_size: int = 5000,
    max_records: int | None = None,
) -> int:
    table_name = "application-links"
    source_object = _source_object(path, object_key=object_key, table_name=table_name)
    sql = """
        INSERT INTO trademark_au.application_link (
            source_row_hash, application_number, link_type, linked_application_number,
            linked_application_country, link_date, source_object_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_row_hash) DO NOTHING
    """

    def rows():
        for row in _iter_trade_mark_rows(path):
            application_number = (row.get("application_number") or "").strip()
            link_type = (row.get("link_type") or "").strip()
            linked = (row.get("linked_application_number") or "").strip()
            if not application_number or not link_type or not linked:
                continue
            yield (
                _row_hash(table_name, row),
                application_number,
                link_type,
                linked,
                (row.get("linked_application_country") or "").strip() or None,
                _date(row.get("link_date")),
                source_object,
            )

    return _batch_execute(
        sql,
        rows(),
        batch_size=batch_size,
        max_records=max_records,
        source_object=source_object,
        pipeline_id="IPGOD_2022_APPLICATION_LINKS_V1",
        table_name=table_name,
    )


def ingest_application_events(
    path: Path,
    *,
    object_key: str | None = None,
    batch_size: int = 5000,
    max_records: int | None = None,
) -> int:
    table_name = "application-events"
    source_object = _source_object(path, object_key=object_key, table_name=table_name)
    sql = """
        INSERT INTO trademark_au.application_event (
            source_row_hash, application_number, event_type, event_category,
            event_effective_date, event_declared_date, is_standing, source_object_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_row_hash) DO NOTHING
    """

    def rows():
        for row in _iter_trade_mark_rows(path):
            application_number = (row.get("application_number") or "").strip()
            event_type = (row.get("event_type") or "").strip()
            if not application_number or not event_type:
                continue
            yield (
                _row_hash(table_name, row),
                application_number,
                event_type,
                (row.get("event_category") or "").strip() or None,
                _date(row.get("event_effective_date")),
                _date(row.get("event_declared_date")),
                _bool(row.get("is_standing")),
                source_object,
            )

    return _batch_execute(
        sql,
        rows(),
        batch_size=batch_size,
        max_records=max_records,
        source_object=source_object,
        pipeline_id="IPGOD_2022_APPLICATION_EVENTS_V1",
        table_name=table_name,
    )


def ingest_application_classification(
    path: Path,
    *,
    object_key: str | None = None,
    batch_size: int = 5000,
    max_records: int | None = None,
) -> int:
    table_name = "application-classification"
    source_object = _source_object(path, object_key=object_key, table_name=table_name)
    sql = """
        INSERT INTO trademark_au.application_classification (
            source_row_hash, application_number, classification_system, classification,
            classification_importance, classification_inventiveness, classification_source,
            classification_date, classification_removal_date, is_current, source_object_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_row_hash) DO NOTHING
    """

    def rows():
        for row in _iter_trade_mark_rows(path):
            application_number = (row.get("application_number") or "").strip()
            if not application_number:
                continue
            yield (
                _row_hash(table_name, row),
                application_number,
                (row.get("classification_system") or "").strip() or None,
                (row.get("classification") or "").strip() or None,
                (row.get("classification_importance") or "").strip() or None,
                (row.get("classification_inventiveness") or "").strip() or None,
                (row.get("classification_source") or "").strip() or None,
                _date(row.get("classification_date")),
                _date(row.get("classification_removal_date")),
                _bool(row.get("is_current")),
                source_object,
            )

    return _batch_execute(
        sql,
        rows(),
        batch_size=batch_size,
        max_records=max_records,
        source_object=source_object,
        pipeline_id="IPGOD_2022_APPLICATION_CLASSIFICATION_V1",
        table_name=table_name,
    )


def ingest_application_description(
    path: Path,
    *,
    object_key: str | None = None,
    batch_size: int = 5000,
    max_records: int | None = None,
) -> int:
    table_name = "application-description"
    source_object = _source_object(path, object_key=object_key, table_name=table_name)
    sql = """
        INSERT INTO trademark_au.application_description (
            source_row_hash, application_number, description_type, description_value,
            source_object_id
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (source_row_hash) DO NOTHING
    """

    def rows():
        for row in _iter_trade_mark_rows(path):
            application_number = (row.get("application_number") or "").strip()
            description_type = (row.get("description_type") or "").strip()
            description_value = (row.get("description_value") or "").strip()
            if not application_number or not description_type or not description_value:
                continue
            yield (
                _row_hash(table_name, row),
                application_number,
                description_type,
                description_value,
                source_object,
            )

    return _batch_execute(
        sql,
        rows(),
        batch_size=batch_size,
        max_records=max_records,
        source_object=source_object,
        pipeline_id="IPGOD_2022_APPLICATION_DESCRIPTION_V1",
        table_name=table_name,
    )
