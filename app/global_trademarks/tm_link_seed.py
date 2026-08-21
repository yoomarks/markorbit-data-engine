from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.ingest_schema import ensure_seed_ingest_schema
from app.global_trademarks.source_objects import register_source_object


_TABLES = {
    "EU": "trademark_eu.tm_link_seed",
    "NZ": "trademark_nz.tm_link_seed",
}
_SOURCE_OFFICE = {"EU": {"EU", "EM"}, "NZ": {"NZ"}}


def _date(value: str | None) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _country(value: str | None) -> str:
    return (value or "").strip().upper()


def _iter_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _validate_jurisdiction(jurisdiction: str) -> str:
    key = jurisdiction.strip().upper()
    if key == "EM":
        key = "EU"
    if key not in _TABLES:
        raise ValueError("TM-Link seed ingestion is supported only for EU and NZ")
    return key


def _record_lineage(
    *,
    jurisdiction: str,
    source_object_id,
    application_numbers: list[str],
    role: str,
) -> None:
    if not application_numbers:
        return
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO acquisition.global_trademark_record_source (
                    jurisdiction, application_number, source_object_id, source_record_role
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                [
                    (jurisdiction, application_number, source_object_id, role)
                    for application_number in application_numbers
                ],
            )
        conn.commit()


def ingest_tm_link_applications(
    path: Path,
    *,
    jurisdiction: str,
    object_key: str | None = None,
    batch_size: int = 5000,
) -> int:
    key = _validate_jurisdiction(jurisdiction)
    ensure_seed_ingest_schema()
    source_object = register_source_object(
        jurisdiction=key,
        source_id=f"TM_LINK_{key}",
        path=path,
        object_key=object_key,
        metadata={"tm_link_table": "applications"},
    )
    table = _TABLES[key]
    if key == "EU":
        sql = f"""
            INSERT INTO {table} (
                application_number, source_status, filed_date, registered_date,
                renewal_due_date, source_object_id
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (application_number) DO UPDATE SET
                source_status = EXCLUDED.source_status,
                filed_date = EXCLUDED.filed_date,
                registered_date = EXCLUDED.registered_date,
                renewal_due_date = EXCLUDED.renewal_due_date
        """
    else:
        sql = f"""
            INSERT INTO {table} (
                application_number, madrid_number, filed_date, registered_date, source_object_id
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (application_number) DO UPDATE SET
                madrid_number = EXCLUDED.madrid_number,
                filed_date = EXCLUDED.filed_date,
                registered_date = EXCLUDED.registered_date
        """

    count = 0
    lineage: list[str] = []
    rows: list[tuple] = []
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for row in _iter_csv(path):
                if _country(row.get("application_country")) not in _SOURCE_OFFICE[key]:
                    continue
                application_number = (row.get("application_number") or "").strip()
                if not application_number:
                    continue
                if key == "EU":
                    values = (
                        application_number,
                        (row.get("current_status") or "").strip() or None,
                        _date(row.get("filing_date")),
                        _date(row.get("registration_date")),
                        _date(row.get("renewal_due_date")),
                        source_object,
                    )
                else:
                    values = (
                        application_number,
                        (row.get("madrid_number") or "").strip() or None,
                        _date(row.get("filing_date")),
                        _date(row.get("registration_date")),
                        source_object,
                    )
                rows.append(values)
                lineage.append(application_number)
                if len(rows) >= batch_size:
                    cur.executemany(sql, rows)
                    count += len(rows)
                    rows.clear()
            if rows:
                cur.executemany(sql, rows)
                count += len(rows)
        conn.commit()
    _record_lineage(
        jurisdiction=key,
        source_object_id=source_object,
        application_numbers=lineage,
        role="TM_LINK_APPLICATIONS",
    )
    return count


def ingest_tm_link_applicants(
    path: Path,
    *,
    jurisdiction: str,
    object_key: str | None = None,
    batch_size: int = 5000,
) -> int:
    key = _validate_jurisdiction(jurisdiction)
    ensure_seed_ingest_schema()
    source_object = register_source_object(
        jurisdiction=key,
        source_id=f"TM_LINK_{key}",
        path=path,
        object_key=object_key,
        metadata={"tm_link_table": "applicants"},
    )
    table = _TABLES[key]
    if key == "EU":
        sql = f"""
            INSERT INTO {table} (
                application_number, applicant_name, applicant_country, source_object_id
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (application_number) DO UPDATE SET
                applicant_name = EXCLUDED.applicant_name,
                applicant_country = EXCLUDED.applicant_country
        """
    else:
        sql = f"""
            INSERT INTO {table} (application_number, applicant_name, source_object_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (application_number) DO UPDATE SET
                applicant_name = EXCLUDED.applicant_name
        """

    count = 0
    lineage: list[str] = []
    rows: list[tuple] = []
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for row in _iter_csv(path):
                if _country(row.get("application_country")) not in _SOURCE_OFFICE[key]:
                    continue
                application_number = (row.get("application_number") or "").strip()
                if not application_number:
                    continue
                applicant_name = (row.get("applicant_name") or "").strip() or None
                if key == "EU":
                    values = (
                        application_number,
                        applicant_name,
                        (row.get("applicant_country") or "").strip() or None,
                        source_object,
                    )
                else:
                    values = (application_number, applicant_name, source_object)
                rows.append(values)
                lineage.append(application_number)
                if len(rows) >= batch_size:
                    cur.executemany(sql, rows)
                    count += len(rows)
                    rows.clear()
            if rows:
                cur.executemany(sql, rows)
                count += len(rows)
        conn.commit()
    _record_lineage(
        jurisdiction=key,
        source_object_id=source_object,
        application_numbers=lineage,
        role="TM_LINK_APPLICANTS",
    )
    return count


def ingest_tm_link_details(
    path: Path,
    *,
    jurisdiction: str,
    object_key: str | None = None,
    batch_size: int = 5000,
) -> int:
    key = _validate_jurisdiction(jurisdiction)
    ensure_seed_ingest_schema()
    source_object = register_source_object(
        jurisdiction=key,
        source_id=f"TM_LINK_{key}",
        path=path,
        object_key=object_key,
        metadata={"tm_link_table": "trademark_details"},
    )
    table = _TABLES[key]
    sql = f"""
        INSERT INTO {table} (application_number, mark_text, source_object_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (application_number) DO UPDATE SET mark_text = EXCLUDED.mark_text
    """
    count = 0
    lineage: list[str] = []
    rows: list[tuple] = []
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for row in _iter_csv(path):
                if _country(row.get("application_country")) not in _SOURCE_OFFICE[key]:
                    continue
                application_number = (row.get("application_number") or "").strip()
                if not application_number:
                    continue
                rows.append(
                    (
                        application_number,
                        (row.get("trademark_text") or "").strip() or None,
                        source_object,
                    )
                )
                lineage.append(application_number)
                if len(rows) >= batch_size:
                    cur.executemany(sql, rows)
                    count += len(rows)
                    rows.clear()
            if rows:
                cur.executemany(sql, rows)
                count += len(rows)
        conn.commit()
    _record_lineage(
        jurisdiction=key,
        source_object_id=source_object,
        application_numbers=lineage,
        role="TM_LINK_DETAILS",
    )
    return count


def ingest_tm_link_classes(
    path: Path,
    *,
    jurisdiction: str,
    object_key: str | None = None,
    batch_size: int = 5000,
) -> int:
    key = _validate_jurisdiction(jurisdiction)
    ensure_seed_ingest_schema()
    source_object = register_source_object(
        jurisdiction=key,
        source_id=f"TM_LINK_{key}",
        path=path,
        object_key=object_key,
        metadata={"tm_link_table": "nice_class"},
    )
    table = _TABLES[key]
    sql = f"""
        INSERT INTO {table} (application_number, nice_classes, source_object_id)
        VALUES (%s, ARRAY[%s]::smallint[], %s)
        ON CONFLICT (application_number) DO UPDATE SET
            nice_classes = CASE
                WHEN EXCLUDED.nice_classes[1] = ANY({table}.nice_classes)
                    THEN {table}.nice_classes
                ELSE array_append({table}.nice_classes, EXCLUDED.nice_classes[1])
            END
    """
    count = 0
    lineage: list[str] = []
    rows: list[tuple] = []
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for row in _iter_csv(path):
                if _country(row.get("application_country")) not in _SOURCE_OFFICE[key]:
                    continue
                application_number = (row.get("application_number") or "").strip()
                raw_class = (row.get("nice_class") or "").strip()
                if not application_number or not raw_class:
                    continue
                nice_class = int(raw_class)
                if not 1 <= nice_class <= 45:
                    continue
                rows.append((application_number, nice_class, source_object))
                lineage.append(application_number)
                if len(rows) >= batch_size:
                    cur.executemany(sql, rows)
                    count += len(rows)
                    rows.clear()
            if rows:
                cur.executemany(sql, rows)
                count += len(rows)
        conn.commit()
    _record_lineage(
        jurisdiction=key,
        source_object_id=source_object,
        application_numbers=lineage,
        role="TM_LINK_CLASSES",
    )
    return count
