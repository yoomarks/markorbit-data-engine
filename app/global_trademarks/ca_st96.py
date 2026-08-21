from __future__ import annotations

import hashlib
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
    for local_name in local_names:
        for child in element.iter():
            if _local_name(child.tag) == local_name:
                text = (child.text or "").strip()
                if text:
                    return text
    return None


def _elements(element: ET.Element, local_name: str) -> list[ET.Element]:
    return [child for child in element.iter() if _local_name(child.tag) == local_name]


def _first_element(element: ET.Element, local_name: str) -> ET.Element | None:
    for child in element.iter():
        if _local_name(child.tag) == local_name:
            return child
    return None


def _texts(element: ET.Element, local_name: str) -> list[str]:
    values: list[str] = []
    for child in element.iter():
        if _local_name(child.tag) != local_name:
            continue
        value = (child.text or "").strip()
        if value:
            values.append(value)
    return values


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


def _bool(value: str | None) -> bool | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    return None


def _smallint(value: str | None) -> int | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        number = int(normalized)
    except ValueError:
        return None
    return number if 1 <= number <= 45 else None


def _application_identity(element: ET.Element) -> tuple[str | None, str]:
    st13 = _first_text(element, "ST13ApplicationNumber")
    if st13:
        digits = "".join(character for character in st13 if character.isdigit())
        if len(digits) >= 15 and digits[:2] == "30":
            serial = digits[-9:]
            application = serial[:-2].lstrip("0") or "0"
            return application, serial[-2:]
    raw = _first_text(element, "ApplicationNumberText", "ApplicationNumber")
    if raw:
        digits = "".join(character for character in raw if character.isdigit())
        if digits:
            return digits.lstrip("0") or "0", "00"
    return None, "00"


def _record_key(application_number: str, extension_counter: str) -> str:
    return f"{application_number}:{extension_counter}"


def _party_payload(
    element: ET.Element,
    *,
    party_role: str,
    party_code: str | None = None,
) -> dict[str, object] | None:
    contact = _first_element(element, "Contact")
    entity_name = _first_element(element, "EntityName")
    postal = _first_element(element, "PostalStructuredAddress")

    party_name = _first_text(element, "LegalEntityName", "EntityName")
    language_code = None
    if contact is not None:
        language_code = _attribute(contact, "languageCode")
    if language_code is None and entity_name is not None:
        language_code = _attribute(entity_name, "languageCode")

    address_lines = _texts(postal, "AddressLineText") if postal is not None else []
    address_region = _first_text(postal, "GeographicRegionName") if postal is not None else None
    address_country = _first_text(postal, "CountryCode") if postal is not None else None
    postal_code = _first_text(postal, "PostalCode") if postal is not None else None
    legal_entity_code = _first_text(element, "NationalLegalEntityCode")

    if not any(
        (
            party_name,
            party_code,
            language_code,
            address_lines,
            address_region,
            address_country,
            postal_code,
            legal_entity_code,
        )
    ):
        return None

    return {
        "party_role": party_role,
        "party_name": party_name,
        "language_code": language_code,
        "party_code": party_code,
        "address_lines": address_lines,
        "address_region": address_region,
        "address_country": address_country,
        "postal_code": postal_code,
        "national_legal_entity_code": legal_entity_code,
    }


def _extract_parties(element: ET.Element) -> list[dict[str, object]]:
    parties: list[dict[str, object]] = []

    for bag in _elements(element, "ApplicantBag"):
        for applicant in _elements(bag, "Applicant"):
            payload = _party_payload(applicant, party_role="CURRENT_OWNER")
            if payload is not None:
                parties.append(payload)

    for representative in _elements(element, "NationalRepresentative"):
        payload = _party_payload(
            representative,
            party_role="TRADEMARK_AGENT",
            party_code=_first_text(representative, "CommentText"),
        )
        if payload is not None:
            parties.append(payload)

    for correspondent in _elements(element, "NationalCorrespondent"):
        payload = _party_payload(
            correspondent,
            party_role="REPRESENTATIVE_FOR_SERVICE",
            party_code=_first_text(correspondent, "CommentText"),
        )
        if payload is not None:
            parties.append(payload)

    for source_index, payload in enumerate(parties, start=1):
        payload["source_index"] = source_index
    return parties


def _extract_goods_services(element: ET.Element) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for bag in _elements(element, "GoodsServicesBag"):
        for class_description in _elements(bag, "ClassDescription"):
            class_number = _smallint(_first_text(class_description, "ClassNumber"))
            classification_version = _first_text(class_description, "ClassificationVersion")

            for text_element in _elements(class_description, "GoodsServicesDescriptionText"):
                text_value = (text_element.text or "").strip()
                if not text_value:
                    continue
                observations.append(
                    {
                        "class_number": class_number,
                        "classification_version": classification_version,
                        "sequence_number": _attribute(text_element, "sequenceNumber"),
                        "language_code": _attribute(text_element, "languageCode"),
                        "text_kind": "GOODS_SERVICES_DESCRIPTION",
                        "text_value": text_value,
                    }
                )

            for text_element in _elements(class_description, "ClassificationTermText"):
                text_value = (text_element.text or "").strip()
                if not text_value:
                    continue
                observations.append(
                    {
                        "class_number": class_number,
                        "classification_version": classification_version,
                        "sequence_number": _attribute(text_element, "sequenceNumber"),
                        "language_code": _attribute(text_element, "languageCode"),
                        "text_kind": "CLASSIFICATION_TERM",
                        "text_value": text_value,
                    }
                )

    for source_index, payload in enumerate(observations, start=1):
        payload["source_index"] = source_index
    return observations


def _extract_events(element: ET.Element) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for bag in _elements(element, "MarkEventBag"):
        for mark_event in _elements(bag, "MarkEvent"):
            national = _first_element(mark_event, "NationalMarkEvent")
            event_scope = national if national is not None else mark_event
            additional_values = _texts(event_scope, "MarkEventAdditionalText")
            payload = {
                "event_category": _first_text(mark_event, "MarkEventCategory"),
                "event_code": _first_text(event_scope, "MarkEventCode"),
                "event_text": _first_text(event_scope, "MarkEventDescriptionText"),
                "event_date": _date(_first_text(mark_event, "MarkEventDate")),
                "response_date": _date(_first_text(mark_event, "MarkEventResponseDate")),
                "additional_text": "\n".join(additional_values) if additional_values else None,
            }
            if any(value is not None for value in payload.values()):
                observations.append(payload)

    for source_index, payload in enumerate(observations, start=1):
        payload["source_index"] = source_index
    return observations


def _extract_relationships(element: ET.Element) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []

    for associated in _elements(element, "AssociatedApplicationNumber"):
        related_application, related_extension = _application_identity(associated)
        if related_application is None:
            continue
        observations.append(
            {
                "relationship_type": "PREVIOUS_ASSOCIATED_APPLICATION",
                "related_application_number": related_application,
                "related_extension_counter": related_extension,
                "related_registration_number": None,
                "related_office_code": _first_text(associated, "IPOfficeCode"),
                "per_se_registration": None,
                "initial_application_date": None,
            }
        )

    for divisional_bag in _elements(element, "DivisionalApplicationBag"):
        initial = _first_element(divisional_bag, "InitialApplicationNumber")
        if initial is None:
            continue
        related_application, related_extension = _application_identity(initial)
        if related_application is None:
            continue
        observations.append(
            {
                "relationship_type": "DIVISIONAL_FROM",
                "related_application_number": related_application,
                "related_extension_counter": related_extension,
                "related_registration_number": None,
                "related_office_code": _first_text(initial, "IPOfficeCode"),
                "per_se_registration": None,
                "initial_application_date": _date(
                    _first_text(divisional_bag, "InitialApplicationDate")
                ),
            }
        )

    for associated_bag in _elements(element, "NationalAssociatedMarkBag"):
        for associated_mark in _elements(associated_bag, "NationalAssociatedMark"):
            related_application, related_extension = _application_identity(associated_mark)
            registration_number = _first_text(associated_mark, "RegistrationNumber")
            if related_application is None and registration_number is None:
                continue
            observations.append(
                {
                    "relationship_type": "NATIONAL_ASSOCIATED_MARK",
                    "related_application_number": related_application,
                    "related_extension_counter": (
                        related_extension if related_application is not None else None
                    ),
                    "related_registration_number": registration_number,
                    "related_office_code": _first_text(associated_mark, "IPOfficeCode"),
                    "per_se_registration": _bool(
                        _first_text(associated_mark, "PerSeRegistration")
                    ),
                    "initial_application_date": None,
                }
            )

    for source_index, payload in enumerate(observations, start=1):
        payload["source_index"] = source_index
    return observations


def _source_row_hash(
    *,
    domain: str,
    source_object: object,
    record_key: str,
    source_index: int,
    payload: dict[str, object],
) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    material = f"{domain}\0{source_object}\0{record_key}\0{source_index}\0{serialized}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _iter_trademarks(handle: BinaryIO) -> Iterator[dict[str, object]]:
    for _event, element in ET.iterparse(handle, events=("end",)):
        if _local_name(element.tag) != "Trademark":
            continue
        application_number, extension_counter = _application_identity(element)
        if application_number:
            operation_category = _operation_category(element)
            rich_update = operation_category == "Update"
            yield {
                "operation_category": operation_category,
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
                    "MarkSignificantVerbalElementText",
                ),
                "mark_category": _first_text(element, "MarkCategory"),
                "source_status": _first_text(element, "MarkCurrentStatusCode"),
                "status_date": _date(_first_text(element, "MarkCurrentStatusDate")),
                "filed_date": _date(_first_text(element, "ApplicationDate")),
                "registered_date": _date(_first_text(element, "RegistrationDate")),
                "expiry_date": _date(_first_text(element, "ExpiryDate")),
                "termination_date": _date(_first_text(element, "TerminationDate")),
                "application_language": _first_text(element, "ApplicationLanguageCode"),
                "parties": _extract_parties(element) if rich_update else [],
                "goods_services": _extract_goods_services(element) if rich_update else [],
                "events": _extract_events(element) if rich_update else [],
                "relationships": _extract_relationships(element) if rich_update else [],
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

_PARTY_SQL = """
    INSERT INTO trademark_ca.party (
        source_row_hash, record_key, application_number, party_role, party_name,
        language_code, party_code, address_lines, address_region, address_country,
        postal_code, national_legal_entity_code, source_index, source_object_id,
        source_payload
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
    )
    ON CONFLICT (source_row_hash) DO NOTHING
"""

_GOODS_SERVICE_SQL = """
    INSERT INTO trademark_ca.goods_service (
        source_row_hash, record_key, application_number, class_number, text_value,
        language_code, classification_version, sequence_number, text_kind,
        source_index, source_object_id, source_payload
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
    ON CONFLICT (source_row_hash) DO NOTHING
"""

_EVENT_SQL = """
    INSERT INTO trademark_ca.event (
        source_row_hash, record_key, application_number, event_code, event_date,
        event_text, event_category, response_date, additional_text, source_index,
        source_object_id, source_payload
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
    ON CONFLICT (source_row_hash) DO NOTHING
"""

_RELATIONSHIP_SQL = """
    INSERT INTO trademark_ca.relationship (
        source_row_hash, record_key, application_number, relationship_type,
        related_application_number, related_extension_counter,
        related_registration_number, related_office_code, per_se_registration,
        initial_application_date, source_index, source_object_id, source_payload
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
    ON CONFLICT (source_row_hash) DO NOTHING
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
    party_rows: list[tuple] = []
    goods_service_rows: list[tuple] = []
    event_rows: list[tuple] = []
    relationship_rows: list[tuple] = []
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

            domain_payloads = (
                ("PARTY", "parties", party_rows),
                ("GOODS_SERVICE", "goods_services", goods_service_rows),
                ("EVENT", "events", event_rows),
                ("RELATIONSHIP", "relationships", relationship_rows),
            )
            for domain, record_field, target_rows in domain_payloads:
                observations = list(record[record_field])
                if observations:
                    lineage_rows.append(
                        (
                            application_number,
                            record_key,
                            source_object,
                            f"CIPO_ST96_{domain}",
                        )
                    )
                for observation in observations:
                    source_index = int(observation["source_index"])
                    row_hash = _source_row_hash(
                        domain=domain,
                        source_object=source_object,
                        record_key=record_key,
                        source_index=source_index,
                        payload=observation,
                    )
                    observation_payload = json.dumps(
                        observation,
                        ensure_ascii=False,
                        default=str,
                    )
                    if domain == "PARTY":
                        target_rows.append(
                            (
                                row_hash,
                                record_key,
                                application_number,
                                observation["party_role"],
                                observation["party_name"],
                                observation["language_code"],
                                observation["party_code"],
                                observation["address_lines"],
                                observation["address_region"],
                                observation["address_country"],
                                observation["postal_code"],
                                observation["national_legal_entity_code"],
                                source_index,
                                source_object,
                                observation_payload,
                            )
                        )
                    elif domain == "GOODS_SERVICE":
                        target_rows.append(
                            (
                                row_hash,
                                record_key,
                                application_number,
                                observation["class_number"],
                                observation["text_value"],
                                observation["language_code"],
                                observation["classification_version"],
                                observation["sequence_number"],
                                observation["text_kind"],
                                source_index,
                                source_object,
                                observation_payload,
                            )
                        )
                    elif domain == "EVENT":
                        target_rows.append(
                            (
                                row_hash,
                                record_key,
                                application_number,
                                observation["event_code"],
                                observation["event_date"],
                                observation["event_text"],
                                observation["event_category"],
                                observation["response_date"],
                                observation["additional_text"],
                                source_index,
                                source_object,
                                observation_payload,
                            )
                        )
                    else:
                        target_rows.append(
                            (
                                row_hash,
                                record_key,
                                application_number,
                                observation["relationship_type"],
                                observation["related_application_number"],
                                observation["related_extension_counter"],
                                observation["related_registration_number"],
                                observation["related_office_code"],
                                observation["per_se_registration"],
                                observation["initial_application_date"],
                                source_index,
                                source_object,
                                observation_payload,
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
    if party_rows:
        cur.executemany(_PARTY_SQL, party_rows)
    if goods_service_rows:
        cur.executemany(_GOODS_SERVICE_SQL, goods_service_rows)
    if event_rows:
        cur.executemany(_EVENT_SQL, event_rows)
    if relationship_rows:
        cur.executemany(_RELATIONSHIP_SQL, relationship_rows)
    cur.executemany(_STATE_UPSERT_SQL, state_rows)
    cur.executemany(_OPERATION_SQL, operation_rows)
    cur.executemany(_LINEAGE_SQL, lineage_rows)


def ingest_cipo_st96_core(
    path: Path,
    *,
    source_id: str = "CIPO_GLOBAL_2025_06_14",
    object_key: str | None = None,
    batch_size: int = 2000,
    max_records: int | None = None,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_records is not None and max_records <= 0:
        raise ValueError("max_records must be positive when provided")
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
        metadata={
            "source_id": source_id,
            "batch_size": batch_size,
            "max_records": max_records,
            "rich_observations": "CIPO_ST96_RICH_OBSERVATION_V1",
        },
    )
    if run.complete:
        return run.rows_committed

    rows_committed = run.rows_committed
    invocation_committed = 0
    checkpoint = run.checkpoint
    records: list[dict[str, object]] = []
    record_position = 0
    bounded_stop = False

    try:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                for record in iter_cipo_records(path):
                    record_position += 1
                    if record_position <= checkpoint:
                        continue
                    records.append(record)
                    limit_reached = bool(
                        max_records is not None
                        and invocation_committed + len(records) >= max_records
                    )
                    if len(records) >= batch_size or limit_reached:
                        _apply_batch(cur, records, source_object)
                        rows_committed += len(records)
                        invocation_committed += len(records)
                        checkpoint = record_position
                        checkpoint_ingest_run(
                            cur,
                            run_id=run.run_id,
                            checkpoint=checkpoint,
                            rows_committed=rows_committed,
                        )
                        conn.commit()
                        records.clear()
                        if limit_reached:
                            bounded_stop = True
                            break

                if records:
                    _apply_batch(cur, records, source_object)
                    rows_committed += len(records)
                    invocation_committed += len(records)
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
