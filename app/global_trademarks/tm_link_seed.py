from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Callable, Iterator

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


def _iter_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _validate_jurisdiction(jurisdiction: str) -> str:
    key = jurisdiction.strip().upper()
    if key == "EM":
        key = "EU"
    if key not in _TABLES:
        raise ValueError("TM-Link seed ingestion is supported only for EU and NZ")
    return key


def _ingest_rows(
    *,
    path: Path,
    jurisdiction: str,
    object_key: str | None,
    batch_size: int,
    table_name: str,
    role: str,
    sql: str,
    values_for: Callable[[dict[str, str], object], tuple | None],
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    key = _validate_jurisdiction(jurisdiction)
    ensure_seed_ingest_schema()
    source_object = register_source_object(
        jurisdiction=key,
        source_id=f"TM_LINK_{key}",
        path=path,
        object_key=object_key,
        metadata={"tm_link_table": table_name},
    )
    lineage_sql = """
        INSERT INTO acquisition.global_trademark_record_source (
            jurisdiction, application_number, source_record_key,
            source_object_id, source_record_role
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """
    count = 0
    rows: list[tuple] = []
    lineage_rows: list[tuple] = []

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for raw in _iter_csv(path):
                if _country(raw.get("application_country")) not in _SOURCE_OFFICE[key]:
                    continue
                application_number = (raw.get("application_number") or "").strip()
                if not application_number:
                    continue
                values = values_for(raw, source_object)
                if values is None:
                    continue
                rows.append(values)
                lineage_rows.append(
                    (key, application_number, application_number, source_object, role)
                )
                if len(rows) >= batch_size:
                    cur.executemany(sql, rows)
                    cur.executemany(lineage_sql, lineage_rows)
                    count += len(rows)
                    rows.clear()
                    lineage_rows.clear()
            if rows:
                cur.executemany(sql, rows)
                cur.executemany(lineage_sql, lineage_rows)
                count += len(rows)
        conn.commit()
    return count


def ingest_tm_link_applications(
    path: Path,
    *,
    jurisdiction: str,
    object_key: str | None = None,
    batch_size: int = 5000,
) -> int:
    key = _validate_jurisdiction(jurisdiction)
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

        def values(raw: dict[str, str], source_object: object) -> tuple:
            return (
                (raw.get("application_number") or "").strip(),
                (raw.get("current_status") or "").strip() or None,
                _date(raw.get("filing_date")),
                _date(raw.get("registration_date")),
                _date(raw.get("renewal_due_date")),
                source_object,
            )

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

        def values(raw: dict[str, str], source_object: object) -> tuple:
            return (
                (raw.get("application_number") or "").strip(),
                (raw.get("madrid_number") or "").strip() or None,
                _date(raw.get("filing_date")),
                _date(raw.get("registration_date")),
                source_object,
            )

    return _ingest_rows(
        path=path,
        jurisdiction=key,
        object_key=object_key,
        batch_size=batch_size,
        table_name="applications",
        role="TM_LINK_APPLICATIONS",
        sql=sql,
        values_for=values,
    )


def ingest_tm_link_applicants(
    path: Path,
    *,
    jurisdiction: str,
    object_key: str | None = None,
    batch_size: int = 5000,
) -> int:
    key = _validate_jurisdiction(jurisdiction)
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

        def values(raw: dict[str, str], source_object: object) -> tuple:
            return (
                (raw.get("application_number") or "").strip(),
                (raw.get("applicant_name") or "").strip() or None,
                (raw.get("applicant_country") or "").strip() or None,
                source_object,
            )

    else:
        sql = f"""
            INSERT INTO {table} (application_number, applicant_name, source_object_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (application_number) DO UPDATE SET
                applicant_name = EXCLUDED.applicant_name
        """

        def values(raw: dict[str, str], source_object: object) -> tuple:
            return (
                (raw.get("application_number") or "").strip(),
                (raw.get("applicant_name") or "").strip() or None,
                source_object,
            )

    return _ingest_rows(
        path=path,
        jurisdiction=key,
        object_key=object_key,
        batch_size=batch_size,
        table_name="applicants",
        role="TM_LINK_APPLICANTS",
        sql=sql,
        values_for=values,
    )


def ingest_tm_link_details(
    path: Path,
    *,
    jurisdiction: str,
    object_key: str | None = None,
    batch_size: int = 5000,
) -> int:
    key = _validate_jurisdiction(jurisdiction)
    table = _TABLES[key]
    sql = f"""
        INSERT INTO {table} (application_number, mark_text, source_object_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (application_number) DO UPDATE SET mark_text = EXCLUDED.mark_text
    """

    def values(raw: dict[str, str], source_object: object) -> tuple:
        return (
            (raw.get("application_number") or "").strip(),
            (raw.get("trademark_text") or "").strip() or None,
            source_object,
        )

    return _ingest_rows(
        path=path,
        jurisdiction=key,
        object_key=object_key,
        batch_size=batch_size,
        table_name="trademark_details",
        role="TM_LINK_DETAILS",
        sql=sql,
        values_for=values,
    )


def ingest_tm_link_classes(
    path: Path,
    *,
    jurisdiction: str,
    object_key: str | None = None,
    batch_size: int = 5000,
) -> int:
    key = _validate_jurisdiction(jurisdiction)
    table = _TABLES[key]
    sql = f"""
        INSERT INTO {table} AS seed (application_number, nice_classes, source_object_id)
        VALUES (%s, ARRAY[%s]::smallint[], %s)
        ON CONFLICT (application_number) DO UPDATE SET
            nice_classes = CASE
                WHEN EXCLUDED.nice_classes[1] = ANY(seed.nice_classes)
                    THEN seed.nice_classes
                ELSE array_append(seed.nice_classes, EXCLUDED.nice_classes[1])
            END
    """

    def values(raw: dict[str, str], source_object: object) -> tuple | None:
        raw_class = (raw.get("nice_class") or "").strip()
        if not raw_class:
            return None
        nice_class = int(raw_class)
        if not 1 <= nice_class <= 45:
            return None
        return (
            (raw.get("application_number") or "").strip(),
            nice_class,
            source_object,
        )

    return _ingest_rows(
        path=path,
        jurisdiction=key,
        object_key=object_key,
        batch_size=batch_size,
        table_name="nice_class",
        role="TM_LINK_CLASSES",
        sql=sql,
        values_for=values,
    )
