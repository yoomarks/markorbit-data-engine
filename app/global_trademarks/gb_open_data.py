from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Iterator

from app.db import postgres_conn
from app.global_trademarks.ingest_runs import (
    begin_or_resume_ingest_run,
    checkpoint_ingest_run,
    complete_ingest_run,
    fail_ingest_run,
)
from app.global_trademarks.ingest_schema import ensure_seed_ingest_schema
from app.global_trademarks.source_objects import register_source_object


UK_FIELDS = (
    "Trade Mark",
    "Mark Text",
    "Name",
    "Postcode",
    "Region",
    "Country",
    "Status",
    "Category of Mark",
    "Mark Type",
    "Series",
    "No of Marks in Series",
    "Filed",
    "Published",
    "Registered",
    "Expired",
    "Renewal Due Date",
)


def _date(value: str | None) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _integer(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def _classes(row: dict[str, str]) -> list[int]:
    classes: list[int] = []
    for number in range(1, 46):
        value = (row.get(f"Class{number}") or "").strip().lower()
        if value not in {"", "0", "false", "no", "n"}:
            classes.append(number)
    return classes


def iter_ukipo_2018(path: Path) -> Iterator[dict[str, object]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="|")
        missing = [field for field in UK_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"UKIPO file missing required columns: {missing}")

        for row in reader:
            application_number = (row.get("Trade Mark") or "").strip()
            if not application_number:
                continue
            yield {
                "application_number": application_number,
                "mark_text": (row.get("Mark Text") or "").strip() or None,
                "applicant_name": (row.get("Name") or "").strip() or None,
                "postcode": (row.get("Postcode") or "").strip() or None,
                "region": (row.get("Region") or "").strip() or None,
                "country": (row.get("Country") or "").strip() or None,
                "source_status": (row.get("Status") or "").strip() or None,
                "mark_category": (row.get("Category of Mark") or "").strip() or None,
                "mark_type": (row.get("Mark Type") or "").strip() or None,
                "series": (row.get("Series") or "").strip() or None,
                "series_count": _integer(row.get("No of Marks in Series")),
                "filed_date": _date(row.get("Filed")),
                "published_date": _date(row.get("Published")),
                "registered_date": _date(row.get("Registered")),
                "expired_date": _date(row.get("Expired")),
                "renewal_due_date": _date(row.get("Renewal Due Date")),
                "nice_classes": _classes(row),
                "source_payload": json.dumps(row, ensure_ascii=False),
            }


def ingest_ukipo_2018(
    path: Path,
    *,
    source_stream: str,
    object_key: str | None = None,
    batch_size: int = 2000,
    max_records: int | None = None,
) -> int:
    if source_stream not in {"DOMESTIC", "MADRID_IR"}:
        raise ValueError("source_stream must be DOMESTIC or MADRID_IR")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_records is not None and max_records <= 0:
        raise ValueError("max_records must be positive when provided")

    ensure_seed_ingest_schema()
    source_object = register_source_object(
        jurisdiction="GB",
        source_id="UKIPO_OPEN_DATA_2018",
        path=path,
        object_key=object_key,
        metadata={"source_stream": source_stream},
    )
    run = begin_or_resume_ingest_run(
        source_object_id=source_object,
        jurisdiction="GB",
        pipeline_id=f"UKIPO_2018_{source_stream}_V1",
        metadata={
            "source_stream": source_stream,
            "batch_size": batch_size,
            "max_records": max_records,
        },
    )
    if run.complete:
        return run.rows_committed

    sql = """
        INSERT INTO trademark_gb.historical_record (
            application_number, mark_text, applicant_name, postcode, region, country,
            source_status, mark_category, mark_type, series, series_count,
            filed_date, published_date, registered_date, expired_date, renewal_due_date,
            nice_classes, source_stream, source_object_id, source_payload
        )
        VALUES (
            %(application_number)s, %(mark_text)s, %(applicant_name)s, %(postcode)s,
            %(region)s, %(country)s, %(source_status)s, %(mark_category)s, %(mark_type)s,
            %(series)s, %(series_count)s, %(filed_date)s, %(published_date)s,
            %(registered_date)s, %(expired_date)s, %(renewal_due_date)s,
            %(nice_classes)s, %(source_stream)s, %(source_object_id)s, %(source_payload)s::jsonb
        )
        ON CONFLICT (application_number) DO UPDATE SET
            mark_text = EXCLUDED.mark_text,
            applicant_name = EXCLUDED.applicant_name,
            postcode = EXCLUDED.postcode,
            region = EXCLUDED.region,
            country = EXCLUDED.country,
            source_status = EXCLUDED.source_status,
            mark_category = EXCLUDED.mark_category,
            mark_type = EXCLUDED.mark_type,
            series = EXCLUDED.series,
            series_count = EXCLUDED.series_count,
            filed_date = EXCLUDED.filed_date,
            published_date = EXCLUDED.published_date,
            registered_date = EXCLUDED.registered_date,
            expired_date = EXCLUDED.expired_date,
            renewal_due_date = EXCLUDED.renewal_due_date,
            nice_classes = EXCLUDED.nice_classes,
            source_stream = EXCLUDED.source_stream,
            source_object_id = EXCLUDED.source_object_id,
            source_payload = EXCLUDED.source_payload
    """
    lineage_sql = """
        INSERT INTO acquisition.global_trademark_record_source (
            jurisdiction, application_number, source_record_key,
            source_object_id, source_record_role
        ) VALUES ('GB', %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """

    rows_committed = run.rows_committed
    invocation_committed = 0
    checkpoint = run.checkpoint
    record_position = 0
    bounded_stop = False
    batch: list[dict[str, object]] = []
    lineage_batch: list[tuple] = []

    try:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                for record in iter_ukipo_2018(path):
                    record_position += 1
                    if record_position <= checkpoint:
                        continue

                    application_number = str(record["application_number"])
                    record["source_stream"] = source_stream
                    record["source_object_id"] = source_object
                    batch.append(record)
                    lineage_batch.append(
                        (
                            application_number,
                            application_number,
                            source_object,
                            f"UKIPO_2018_{source_stream}",
                        )
                    )
                    limit_reached = bool(
                        max_records is not None
                        and invocation_committed + len(batch) >= max_records
                    )
                    if len(batch) >= batch_size or limit_reached:
                        cur.executemany(sql, batch)
                        cur.executemany(lineage_sql, lineage_batch)
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
                        lineage_batch.clear()
                        if limit_reached:
                            bounded_stop = True
                            break

                if batch:
                    cur.executemany(sql, batch)
                    cur.executemany(lineage_sql, lineage_batch)
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
