from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterator

from app.db import postgres_conn
from app.global_trademarks.ingest_runs import (
    begin_or_resume_ingest_run,
    checkpoint_ingest_run,
    complete_ingest_run,
    fail_ingest_run,
)
from app.global_trademarks.ingest_schema import ensure_seed_ingest_schema
from app.global_trademarks.source_objects import register_source_object


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(element: ET.Element, *local_names: str) -> str | None:
    wanted = set(local_names)
    for child in element.iter():
        if _local_name(child.tag) in wanted:
            text = (child.text or "").strip()
            if text:
                return text
    return None


def _attribute(element: ET.Element, local_name: str) -> str | None:
    for name, value in element.attrib.items():
        if _local_name(name) == local_name:
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None


def _operation_category(element: ET.Element) -> str:
    raw = _attribute(element, "operationCategory") or "Update"
    normalized = raw.strip().lower()
    if normalized == "update":
        return "Update"
    if normalized == "delete":
        return "Delete"
    raise ValueError(f"unsupported CIPO trademark operationCategory: {raw}")


def _date(value: str | None) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _application_identity(element: ET.Element) -> tuple[str | None, str]:
    st13 = _first_text(element, "ST13ApplicationNumber")
    if st13:
        digits = "".join(character for character in st13 if character.isdigit())
        if len(digits) >= 15 and digits[:2] == "30":
            serial = digits[-9:]
            application = serial[:-2].lstrip("0") or "0"
            return application, serial[-2:]
    raw = _first_text(element, "ApplicationNumber")
    if raw and raw.isdigit():
        return raw.lstrip("0") or "0", "00"
    return None, "00"


def _record_key(application_number: str, extension_counter: str) -> str:
    return f"{application_number}:{extension_counter}"


def _iter_trademarks(handle: BinaryIO) -> Iterator[dict[str, object]]:
    for _event, element in ET.iterparse(handle, events=("end",)):
        if _local_name(element.tag) != "Trademark":
            continue
        application_number, extension_counter = _application_identity(element)
        if application_number:
            yield {
                "operation_category": _operation_category(element),
                "record_key": _record_key(application_number, extension_counter),
                "application_number": application_number,
                "extension_counter": extension_counter,
                "registration_number": _first_text(element, "RegistrationNumber"),
                "international_registration_number": _first_text(
                    element, "InternationalMarkIdentifier"
                ),
                "mark_text": _first_text(
                    element,
                    "MarkVerbalElementText",
                    "MarkName",
                    "MarkLiteralElement",
                ),
                "mark_category": _first_text(element, "MarkCategory"),
                "source_status": _first_text(element, "MarkCurrentStatusCode"),
                "status_date": _date(_first_text(element, "MarkCurrentStatusDate")),
                "filed_date": _date(_first_text(element, "ApplicationDate")),
                "registered_date": _date(_first_text(element, "RegistrationDate")),
                "expiry_date": _date(_first_text(element, "ExpiryDate")),
                "termination_date": _date(_first_text(element, "TerminationDate")),
                "application_language": _first_text(element, "ApplicationLanguageCode"),
            }
        element.clear()


def iter_cipo_records(path: Path) -> Iterator[dict[str, object]]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".xml")]
            if not members:
                raise ValueError(f"CIPO archive contains no XML members: {path}")
            for member in members:
                with archive.open(member) as handle:
                    yield from _iter_trademarks(handle)
        return

    with path.open("rb") as handle:
        yield from _iter_trademarks(handle)


_RECORD_UPSERT_SQL = """
    INSERT INTO trademark_ca.st96_record (
        record_key, application_number, extension_counter, registration_number,
        international_registration_number, mark_text, mark_category, source_status,
        status_date, filed_date, registered_date, expiry_date, termination_date,
        application_language, source_object_id, source_payload
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s::jsonb
    )
    ON CONFLICT (record_key) DO UPDATE SET
        application_number = EXCLUDED.application_number,
        extension_counter = EXCLUDED.extension_counter,
        registration_number = EXCLUDED.registration_number,
        international_registration_number = EXCLUDED.international_registration_number,
        mark_text = EXCLUDED.mark_text,
        mark_category = EXCLUDED.mark_category,
        source_status = EXCLUDED.source_status,
        status_date = EXCLUDED.status_date,
        filed_date = EXCLUDED.filed_date,
        registered_date = EXCLUDED.registered_date,
        expiry_date = EXCLUDED.expiry_date,
        termination_date = EXCLUDED.termination_date,
        application_language = EXCLUDED.application_language,
        source_object_id = EXCLUDED.source_object_id,
        source_payload = EXCLUDED.source_payload
"""

_STATE_UPSERT_SQL = """
    INSERT INTO trademark_ca.record_state (
        record_key, application_number, extension_counter, source_present,
        last_operation_category, last_source_object_id, observed_at
    ) VALUES (%s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (record_key) DO UPDATE SET
        application_number = EXCLUDED.application_number,
        extension_counter = EXCLUDED.extension_counter,
        source_present = EXCLUDED.source_present,
        last_operation_category = EXCLUDED.last_operation_category,
        last_source_object_id = EXCLUDED.last_source_object_id,
        observed_at = now()
"""

_OPERATION_SQL = """
    INSERT INTO trademark_ca.record_operation (
        source_object_id, record_key, application_number, extension_counter,
        operation_category, payload
    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
    ON CONFLICT DO NOTHING
"""

_LINEAGE_SQL = """
    INSERT INTO acquisition.global_trademark_record_source (
        jurisdiction, application_number, source_record_key,
        source_object_id, source_record_role
    ) VALUES ('CA', %s, %s, %s, %s)
    ON CONFLICT DO NOTHING
"""


def _apply_batch(cur, records: list[dict[str, object]], source_object) -> None:
    update_rows: list[tuple] = []
    state_rows: list[tuple] = []
    operation_rows: list[tuple] = []
    lineage_rows: list[tuple] = []

    for record in records:
        application_number = str(record["application_number"])
        record_key = str(record["record_key"])
        operation_category = str(record["operation_category"])
        payload = json.dumps(record, ensure_ascii=False, default=str)

        if operation_category == "Update":
            update_rows.append(
                (
                    record_key,
                    application_number,
                    record["extension_counter"],
                    record["registration_number"],
                    record["international_registration_number"],
                    record["mark_text"],
                    record["mark_category"],
                    record["source_status"],
                    record["status_date"],
                    record["filed_date"],
                    record["registered_date"],
                    record["expiry_date"],
                    record["termination_date"],
                    record["application_language"],
                    source_object,
                    payload,
                )
            )

        state_rows.append(
            (
                record_key,
                application_number,
                record["extension_counter"],
                operation_category == "Update",
                operation_category,
                source_object,
            )
        )
        operation_rows.append(
            (
                source_object,
                record_key,
                application_number,
                record["extension_counter"],
                operation_category,
                payload,
            )
        )
        lineage_rows.append(
            (
                application_number,
                record_key,
                source_object,
                f"CIPO_ST96_{operation_category.upper()}",
            )
        )

    if update_rows:
        cur.executemany(_RECORD_UPSERT_SQL, update_rows)
    cur.executemany(_STATE_UPSERT_SQL, state_rows)
    cur.executemany(_OPERATION_SQL, operation_rows)
    cur.executemany(_LINEAGE_SQL, lineage_rows)


def ingest_cipo_st96_core(
    path: Path,
    *,
    source_id: str = "CIPO_GLOBAL_2025_06_14",
    object_key: str | None = None,
    batch_size: int = 2000,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if source_id not in {"CIPO_GLOBAL_2025_06_14", "CIPO_WEEKLY"}:
        raise ValueError("unsupported CIPO source_id")

    ensure_seed_ingest_schema()
    source_object = register_source_object(
        jurisdiction="CA",
        source_id=source_id,
        path=path,
        object_key=object_key,
        metadata={"format": "WIPO_ST96_XML"},
    )
    run = begin_or_resume_ingest_run(
        source_object_id=source_object,
        jurisdiction="CA",
        pipeline_id="CIPO_ST96_CORE_V1",
        metadata={"source_id": source_id, "batch_size": batch_size},
    )
    if run.complete:
        return run.rows_committed

    rows_committed = run.rows_committed
    checkpoint = run.checkpoint
    records: list[dict[str, object]] = []
    record_position = 0

    try:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                for record in iter_cipo_records(path):
                    record_position += 1
                    if record_position <= checkpoint:
                        continue
                    records.append(record)
                    if len(records) >= batch_size:
                        _apply_batch(cur, records, source_object)
                        rows_committed += len(records)
                        checkpoint = record_position
                        checkpoint_ingest_run(
                            cur,
                            run_id=run.run_id,
                            checkpoint=checkpoint,
                            rows_committed=rows_committed,
                        )
                        conn.commit()
                        records.clear()

                if records:
                    _apply_batch(cur, records, source_object)
                    rows_committed += len(records)
                    checkpoint = record_position

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
