from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterator

from app.db import postgres_conn
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
    sql = """
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
    lineage_sql = """
        INSERT INTO acquisition.global_trademark_record_source (
            jurisdiction, application_number, source_record_key,
            source_object_id, source_record_role
        ) VALUES ('CA', %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """

    count = 0
    rows: list[tuple] = []
    lineage_rows: list[tuple] = []
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for record in iter_cipo_records(path):
                application_number = str(record["application_number"])
                record_key = str(record["record_key"])
                rows.append(
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
                        json.dumps(record, ensure_ascii=False, default=str),
                    )
                )
                lineage_rows.append(
                    (application_number, record_key, source_object, "CIPO_ST96_CORE")
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
