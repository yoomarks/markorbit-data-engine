from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import uuid

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.contact_store import CONTACT_PERSON_NAMESPACE, CONTACT_RELATION_NAMESPACE
from app.contact_ingest.normalization import (
    clean_text,
    normalize_channel,
    normalize_credit_code,
    normalize_header,
    normalized_match_text,
    sha256_text,
    split_values,
)
from app.cn_qcc.migrations import ensure_qcc_schema
from app.db import postgres_conn


_ALLOWED_RESULT_STATUS = {"SUCCESS", "NOT_FOUND", "FAILED", "UNATTEMPTED"}
_HEADER_ALIASES = {
    "taskid": "task_id",
    "任务id": "task_id",
    "entityid": "entity_id",
    "实体id": "entity_id",
    "resultstatus": "result_status",
    "状态": "result_status",
    "fetchedat": "fetched_at",
    "采集时间": "fetched_at",
    "qcccompanyid": "qcc_company_id",
    "企查查id": "qcc_company_id",
    "companyname": "company_name",
    "企业名称": "company_name",
    "unifiedsocialcreditcode": "unified_social_credit_code",
    "creditcode": "unified_social_credit_code",
    "统一社会信用代码": "unified_social_credit_code",
    "legalrepresentative": "legal_representative",
    "法定代表人": "legal_representative",
    "registrationstatus": "registration_status",
    "登记状态": "registration_status",
    "registeredcapital": "registered_capital",
    "注册资本": "registered_capital",
    "establishmentdate": "establishment_date",
    "成立日期": "establishment_date",
    "registeredaddress": "registered_address",
    "注册地址": "registered_address",
    "businessscope": "business_scope",
    "经营范围": "business_scope",
    "phones": "phones",
    "phone": "phones",
    "电话": "phones",
    "emails": "emails",
    "email": "emails",
    "邮箱": "emails",
    "websites": "websites",
    "website": "websites",
    "官网": "websites",
    "contactname": "contact_name",
    "联系人": "contact_name",
    "contacttitle": "contact_title",
    "联系人职务": "contact_title",
    "contactphones": "contact_phones",
    "contactphone": "contact_phones",
    "联系人电话": "contact_phones",
    "contactemails": "contact_emails",
    "contactemail": "contact_emails",
    "联系人邮箱": "contact_emails",
    "error": "error_message",
    "errormessage": "error_message",
    "错误": "error_message",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_datetime(value: str) -> datetime:
    text = clean_text(value)
    if not text:
        return datetime.now(timezone.utc)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: str) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    for candidate in (text, text.replace("/", "-"), text.replace(".", "-")):
        try:
            return date.fromisoformat(candidate[:10])
        except ValueError:
            continue
    raise ValueError(f"invalid establishment_date: {text!r}")


def _canonicalize_row(row: dict[str, str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for raw_key, raw_value in row.items():
        key = normalize_header(raw_key)
        canonical = _HEADER_ALIASES.get(key, clean_text(raw_key))
        if canonical:
            output[canonical] = clean_text(raw_value)
    return output


def _read_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("QCC result CSV has no header")
        rows: list[tuple[int, dict[str, str]]] = []
        for source_row, row in enumerate(reader, start=2):
            canonical = _canonicalize_row({str(k): str(v or "") for k, v in row.items() if k is not None})
            if any(canonical.values()):
                rows.append((source_row, canonical))
    return rows


def _upsert_contact_source(cur, *, source_sha256: str, source_name: str, batch_id: str) -> uuid.UUID:
    cur.execute(
        """
        INSERT INTO contact.source (
            source_sha256, source_name, file_type, source_profile, source_segment,
            source_scope, default_country_code, ingest_version
        ) VALUES (%s, %s, 'CSV', 'QICHACHA_COMPANY_ENRICHMENT',
                  'CN_COMPANY_APPLICANT', %s, 'CN', %s)
        ON CONFLICT (source_sha256) DO UPDATE SET
            source_name = EXCLUDED.source_name,
            source_profile = EXCLUDED.source_profile,
            source_segment = EXCLUDED.source_segment,
            source_scope = EXCLUDED.source_scope,
            default_country_code = EXCLUDED.default_country_code,
            ingest_version = EXCLUDED.ingest_version,
            last_seen_at = now()
        RETURNING source_id
        """,
        (source_sha256, source_name, batch_id, CONTACT_INGEST_VERSION),
    )
    return cur.fetchone()["source_id"]


def _create_import_run(cur, source_id: uuid.UUID) -> uuid.UUID:
    cur.execute(
        """
        INSERT INTO contact.import_run(source_id, status, apply_mode)
        VALUES (%s, 'RUNNING', true)
        RETURNING run_id
        """,
        (source_id,),
    )
    return cur.fetchone()["run_id"]


def _upsert_raw_record(
    cur,
    *,
    source_id: uuid.UUID,
    batch_id: str,
    source_row: int,
    entity_id: uuid.UUID,
    raw_fields: dict[str, str],
) -> uuid.UUID:
    cur.execute(
        """
        INSERT INTO contact.raw_record (
            source_id, source_member, sheet_name, source_row, source_profile,
            entity_id, entity_match_method, entity_match_confidence, raw_data
        ) VALUES (%s, %s, '', %s, 'QICHACHA_COMPANY_ENRICHMENT',
                  %s, 'EXPORTED_TASK_ENTITY', 0.9900, %s::jsonb)
        ON CONFLICT (source_id, source_member, sheet_name, source_row)
        DO UPDATE SET
            entity_id = EXCLUDED.entity_id,
            entity_match_method = EXCLUDED.entity_match_method,
            entity_match_confidence = EXCLUDED.entity_match_confidence,
            raw_data = EXCLUDED.raw_data,
            updated_at = now()
        RETURNING raw_record_id
        """,
        (source_id, batch_id, source_row, entity_id, json.dumps(raw_fields, ensure_ascii=False)),
    )
    return cur.fetchone()["raw_record_id"]


def _upsert_identifier(cur, *, entity_id: uuid.UUID, credit_code: str) -> None:
    normalized = normalize_credit_code(credit_code)
    if not normalized:
        return
    cur.execute(
        """
        SELECT entity_id FROM entity.entity_identifier
        WHERE identifier_type = 'CN_UNIFIED_SOCIAL_CREDIT_CODE'
          AND normalized_value = %s
          AND COALESCE(country_code, '') = 'CN'
        """,
        (normalized,),
    )
    existing = cur.fetchone()
    if existing and existing["entity_id"] != entity_id:
        raise ValueError(
            "QCC unified social credit code conflicts with a different Entity Hub entity"
        )
    cur.execute(
        """
        INSERT INTO entity.entity_identifier (
            entity_id, identifier_type, identifier_value, normalized_value,
            country_code, source, confidence_score
        ) VALUES (%s, 'CN_UNIFIED_SOCIAL_CREDIT_CODE', %s, %s, 'CN', 'QICHACHA', 0.9800)
        ON CONFLICT (identifier_type, normalized_value, COALESCE(country_code, ''))
        DO UPDATE SET last_seen_at = now(), source = 'QICHACHA', confidence_score = 0.9800
        """,
        (entity_id, credit_code, normalized),
    )


def _upsert_person(
    cur,
    *,
    entity_id: uuid.UUID,
    name: str,
    relation_type: str,
    title: str,
    source_id: uuid.UUID,
) -> uuid.UUID | None:
    normalized = normalized_match_text(name)
    if not normalized:
        return None
    material = f"{entity_id}|{normalized}"
    person_id = uuid.uuid5(CONTACT_PERSON_NAMESPACE, material)
    person_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    cur.execute(
        """
        INSERT INTO contact.person AS current_person (
            person_id, person_key, canonical_name, normalized_name, country_code
        ) VALUES (%s, %s, %s, %s, 'CN')
        ON CONFLICT (person_id) DO UPDATE SET
            canonical_name = CASE
                WHEN length(EXCLUDED.canonical_name) > length(current_person.canonical_name)
                THEN EXCLUDED.canonical_name ELSE current_person.canonical_name END,
            country_code = COALESCE(current_person.country_code, EXCLUDED.country_code),
            updated_at = now()
        """,
        (person_id, person_key, name, normalized),
    )
    relation_material = f"{entity_id}|{person_id}|{relation_type}"
    relation_id = uuid.uuid5(CONTACT_RELATION_NAMESPACE, relation_material)
    cur.execute(
        """
        INSERT INTO contact.entity_person_relation AS current_relation (
            relation_id, entity_id, person_id, relation_type, title,
            confidence_score, first_source_id, last_source_id
        ) VALUES (%s, %s, %s, %s, %s, 0.9000, %s, %s)
        ON CONFLICT (entity_id, person_id, relation_type) DO UPDATE SET
            title = CASE WHEN EXCLUDED.title <> '' THEN EXCLUDED.title ELSE current_relation.title END,
            last_source_id = EXCLUDED.last_source_id,
            last_seen_at = now()
        """,
        (relation_id, entity_id, person_id, relation_type, title, source_id, source_id),
    )
    return person_id


def _upsert_channel(
    cur,
    *,
    source_id: uuid.UUID,
    raw_record_id: uuid.UUID,
    owner_entity_id: uuid.UUID | None,
    owner_person_id: uuid.UUID | None,
    channel_type: str,
    raw_value: str,
    source_column: str,
) -> None:
    actual_type, normalized = normalize_channel(channel_type, raw_value, country_code="CN")
    if not actual_type or not normalized:
        return
    if bool(owner_entity_id) == bool(owner_person_id):
        raise ValueError("QCC channel must have exactly one owner")
    if owner_entity_id:
        cur.execute(
            """
            INSERT INTO contact.channel (
                entity_id, channel_type, channel_value, normalized_value,
                verification_status, verification_score
            ) VALUES (%s, %s, %s, %s, 'SOURCE_OBSERVED', 0.8500)
            ON CONFLICT DO NOTHING
            RETURNING channel_id
            """,
            (owner_entity_id, actual_type, raw_value, normalized),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                """
                UPDATE contact.channel
                SET last_seen_at = now(), verification_status = 'SOURCE_OBSERVED',
                    verification_score = GREATEST(verification_score, 0.8500)
                WHERE entity_id = %s AND channel_type = %s AND normalized_value = %s
                RETURNING channel_id
                """,
                (owner_entity_id, actual_type, normalized),
            )
            row = cur.fetchone()
    else:
        cur.execute(
            """
            INSERT INTO contact.channel (
                person_id, channel_type, channel_value, normalized_value,
                verification_status, verification_score
            ) VALUES (%s, %s, %s, %s, 'SOURCE_OBSERVED', 0.8500)
            ON CONFLICT DO NOTHING
            RETURNING channel_id
            """,
            (owner_person_id, actual_type, raw_value, normalized),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                """
                UPDATE contact.channel
                SET last_seen_at = now(), verification_status = 'SOURCE_OBSERVED',
                    verification_score = GREATEST(verification_score, 0.8500)
                WHERE person_id = %s AND channel_type = %s AND normalized_value = %s
                RETURNING channel_id
                """,
                (owner_person_id, actual_type, normalized),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError("failed to resolve QCC contact channel")
    channel_id = row["channel_id"]
    observation_key = sha256_text(
        source_id, raw_record_id, source_column, actual_type, normalized
    )
    cur.execute(
        """
        INSERT INTO contact.channel_observation (
            observation_key, channel_id, source_id, raw_record_id,
            source_column, raw_value, confidence_score, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, 0.8500, %s::jsonb)
        ON CONFLICT (observation_key) DO NOTHING
        """,
        (
            observation_key,
            channel_id,
            source_id,
            raw_record_id,
            source_column,
            raw_value,
            json.dumps({"source": "QICHACHA"}),
        ),
    )


def _normalize_values(values: list[str], channel_type: str) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        _, normalized = normalize_channel(channel_type, raw, country_code="CN")
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def _snapshot_material(task: dict[str, object], row: dict[str, str]) -> dict[str, object]:
    return {
        "entity_id": str(task["entity_id"]),
        "qcc_company_id": row.get("qcc_company_id", ""),
        "company_name": row.get("company_name", ""),
        "unified_social_credit_code": normalize_credit_code(row.get("unified_social_credit_code", "")),
        "legal_representative": row.get("legal_representative", ""),
        "registration_status": row.get("registration_status", ""),
        "registered_capital": row.get("registered_capital", ""),
        "establishment_date": row.get("establishment_date", ""),
        "registered_address": row.get("registered_address", ""),
        "business_scope": row.get("business_scope", ""),
        "phones": _normalize_values(split_values(row.get("phones", "")), "PHONE"),
        "emails": _normalize_values(split_values(row.get("emails", "")), "EMAIL"),
        "websites": _normalize_values(split_values(row.get("websites", "")), "WEBSITE"),
        "contact_name": row.get("contact_name", ""),
        "contact_title": row.get("contact_title", ""),
        "contact_phones": _normalize_values(split_values(row.get("contact_phones", "")), "PHONE"),
        "contact_emails": _normalize_values(split_values(row.get("contact_emails", "")), "EMAIL"),
    }


def _persist_success(
    cur,
    *,
    task: dict[str, object],
    row: dict[str, str],
    source_row: int,
    fetched_at: datetime,
    result_path: Path,
    result_sha256: str,
    contact_source_id: uuid.UUID,
    raw_record_id: uuid.UUID,
) -> str:
    entity_id = task["entity_id"]
    material = _snapshot_material(task, row)
    payload = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    observation_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    cur.execute(
        "SELECT observation_hash FROM acquisition.cn_qcc_company_observation WHERE task_id = %s",
        (task["task_id"],),
    )
    existing = cur.fetchone()
    if existing and existing["observation_hash"] != observation_hash:
        raise ValueError("same QCC task was re-imported with a different successful snapshot")

    credit_code = row.get("unified_social_credit_code", "")
    _upsert_identifier(cur, entity_id=entity_id, credit_code=credit_code)

    phones_raw = split_values(row.get("phones", ""))
    emails_raw = split_values(row.get("emails", ""))
    websites_raw = split_values(row.get("websites", ""))
    for value in phones_raw:
        _upsert_channel(
            cur, source_id=contact_source_id, raw_record_id=raw_record_id,
            owner_entity_id=entity_id, owner_person_id=None,
            channel_type="PHONE", raw_value=value, source_column="phones",
        )
    for value in emails_raw:
        _upsert_channel(
            cur, source_id=contact_source_id, raw_record_id=raw_record_id,
            owner_entity_id=entity_id, owner_person_id=None,
            channel_type="EMAIL", raw_value=value, source_column="emails",
        )
    for value in websites_raw:
        _upsert_channel(
            cur, source_id=contact_source_id, raw_record_id=raw_record_id,
            owner_entity_id=entity_id, owner_person_id=None,
            channel_type="WEBSITE", raw_value=value, source_column="websites",
        )

    legal_name = row.get("legal_representative", "")
    if legal_name:
        _upsert_person(
            cur,
            entity_id=entity_id,
            name=legal_name,
            relation_type="LEGAL_REPRESENTATIVE",
            title="法定代表人",
            source_id=contact_source_id,
        )

    contact_name = row.get("contact_name", "")
    contact_person_id = None
    if contact_name:
        contact_person_id = _upsert_person(
            cur,
            entity_id=entity_id,
            name=contact_name,
            relation_type="CONTACT_PERSON",
            title=row.get("contact_title", ""),
            source_id=contact_source_id,
        )
    if contact_person_id:
        for value in split_values(row.get("contact_phones", "")):
            _upsert_channel(
                cur, source_id=contact_source_id, raw_record_id=raw_record_id,
                owner_entity_id=None, owner_person_id=contact_person_id,
                channel_type="PHONE", raw_value=value, source_column="contact_phones",
            )
        for value in split_values(row.get("contact_emails", "")):
            _upsert_channel(
                cur, source_id=contact_source_id, raw_record_id=raw_record_id,
                owner_entity_id=None, owner_person_id=contact_person_id,
                channel_type="EMAIL", raw_value=value, source_column="contact_emails",
            )

    if not existing:
        cur.execute(
            """
            INSERT INTO acquisition.cn_qcc_company_observation (
                task_id, batch_id, entity_id, observation_hash, qcc_company_id,
                company_name, unified_social_credit_code, legal_representative,
                registration_status, registered_capital, establishment_date,
                registered_address, business_scope, phones, emails, websites,
                contact_name, contact_title, contact_phones, contact_emails,
                source_result_path, source_result_sha256, source_row, raw_fields, observed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
            )
            """,
            (
                task["task_id"], task["batch_id"], entity_id, observation_hash,
                row.get("qcc_company_id", ""), row.get("company_name", ""),
                credit_code, legal_name, row.get("registration_status", ""),
                row.get("registered_capital", ""), _parse_date(row.get("establishment_date", "")),
                row.get("registered_address", ""), row.get("business_scope", ""),
                material["phones"], material["emails"], material["websites"],
                contact_name, row.get("contact_title", ""),
                material["contact_phones"], material["contact_emails"],
                str(result_path), result_sha256, source_row,
                json.dumps(row, ensure_ascii=False), fetched_at,
            ),
        )
    return observation_hash


def _update_coverage(
    cur,
    *,
    task: dict[str, object],
    result_status: str,
    fetched_at: datetime,
    snapshot_hash: str | None,
    refresh_days: int,
) -> None:
    success = result_status in {"SUCCESS", "NOT_FOUND"}
    refresh_due = fetched_at + timedelta(days=refresh_days) if success else None
    cur.execute(
        """
        INSERT INTO acquisition.cn_qcc_company_coverage AS current (
            entity_id, source_fingerprint, first_fetched_at, last_fetched_at,
            last_result_status, last_snapshot_hash, refresh_due_at, last_batch_id,
            successful_fetch_count
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (entity_id) DO UPDATE SET
            source_fingerprint = EXCLUDED.source_fingerprint,
            first_fetched_at = COALESCE(current.first_fetched_at, EXCLUDED.first_fetched_at),
            last_fetched_at = COALESCE(EXCLUDED.last_fetched_at, current.last_fetched_at),
            last_result_status = EXCLUDED.last_result_status,
            last_snapshot_hash = COALESCE(EXCLUDED.last_snapshot_hash, current.last_snapshot_hash),
            refresh_due_at = EXCLUDED.refresh_due_at,
            last_batch_id = EXCLUDED.last_batch_id,
            successful_fetch_count = current.successful_fetch_count + %s,
            updated_at = now()
        """,
        (
            task["entity_id"],
            task["source_fingerprint"],
            fetched_at if success else None,
            fetched_at if success else None,
            result_status,
            snapshot_hash,
            refresh_due,
            task["batch_id"],
            1 if success else 0,
            1 if success else 0,
        ),
    )


def ingest_result(batch_id: str, result_path: Path) -> dict[str, object]:
    ensure_qcc_schema()
    result_path = result_path.resolve()
    result_sha256 = _sha256_file(result_path)
    incoming_rows = _read_rows(result_path)

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM acquisition.cn_qcc_batch
                WHERE batch_id = %s FOR UPDATE
                """,
                (batch_id,),
            )
            batch = cur.fetchone()
            if not batch:
                raise ValueError(f"unknown CN QCC batch: {batch_id}")
            if batch["status"] == "COMPLETED":
                if str(batch.get("result_sha256") or "") != result_sha256:
                    raise ValueError("completed QCC batch was supplied a different result file")
                return {"batch_id": batch_id, "status": "COMPLETED", "idempotent": True}
            if batch["status"] != "EXPORTED":
                raise ValueError(f"CN QCC result requires EXPORTED batch, got {batch['status']}")

            cur.execute(
                "SELECT * FROM acquisition.cn_qcc_task WHERE batch_id = %s",
                (batch_id,),
            )
            tasks = {str(row["task_id"]): row for row in cur.fetchall()}
            seen: set[str] = set()
            normalized_rows: list[tuple[int, dict[str, str], dict[str, object]]] = []
            for source_row, row in incoming_rows:
                task_id = clean_text(row.get("task_id", ""))
                if not task_id:
                    raise ValueError(f"QCC result row {source_row} is missing task_id")
                if task_id in seen:
                    raise ValueError(f"duplicate QCC task_id in result: {task_id}")
                seen.add(task_id)
                task = tasks.get(task_id)
                if not task:
                    raise ValueError(f"unexpected QCC task_id in result: {task_id}")
                supplied_entity = clean_text(row.get("entity_id", ""))
                if supplied_entity and str(uuid.UUID(supplied_entity)) != str(task["entity_id"]):
                    raise ValueError(f"QCC result entity_id does not match task {task_id}")
                status = clean_text(row.get("result_status", "")).upper()
                if status not in _ALLOWED_RESULT_STATUS:
                    raise ValueError(f"invalid QCC result_status for task {task_id}: {status!r}")
                normalized_rows.append((source_row, row, task))

            contact_source_id = _upsert_contact_source(
                cur,
                source_sha256=result_sha256,
                source_name=result_path.name,
                batch_id=batch_id,
            )
            import_run_id = _create_import_run(cur, contact_source_id)
            metrics = {"SUCCESS": 0, "NOT_FOUND": 0, "FAILED": 0, "UNATTEMPTED": 0}

            for source_row, row, task in normalized_rows:
                status = row["result_status"].upper()
                fetched_at = _parse_datetime(row.get("fetched_at", ""))
                raw_record_id = _upsert_raw_record(
                    cur,
                    source_id=contact_source_id,
                    batch_id=batch_id,
                    source_row=source_row,
                    entity_id=task["entity_id"],
                    raw_fields=row,
                )
                snapshot_hash: str | None = None
                if status == "SUCCESS":
                    snapshot_hash = _persist_success(
                        cur,
                        task=task,
                        row=row,
                        source_row=source_row,
                        fetched_at=fetched_at,
                        result_path=result_path,
                        result_sha256=result_sha256,
                        contact_source_id=contact_source_id,
                        raw_record_id=raw_record_id,
                    )
                _update_coverage(
                    cur,
                    task=task,
                    result_status=status,
                    fetched_at=fetched_at,
                    snapshot_hash=snapshot_hash,
                    refresh_days=int(batch["refresh_days"]),
                )
                cur.execute(
                    """
                    UPDATE acquisition.cn_qcc_task
                    SET state = %s, result_status = %s, fetched_at = %s,
                        snapshot_hash = %s, error_message = NULLIF(%s, ''), completed_at = now()
                    WHERE task_id = %s
                    """,
                    (status, status, fetched_at, snapshot_hash, row.get("error_message", ""), task["task_id"]),
                )
                metrics[status] += 1

            missing_task_ids = [task_id for task_id in tasks if task_id not in seen]
            for task_id in missing_task_ids:
                task = tasks[task_id]
                fetched_at = datetime.now(timezone.utc)
                _update_coverage(
                    cur,
                    task=task,
                    result_status="UNATTEMPTED",
                    fetched_at=fetched_at,
                    snapshot_hash=None,
                    refresh_days=int(batch["refresh_days"]),
                )
                cur.execute(
                    """
                    UPDATE acquisition.cn_qcc_task
                    SET state = 'UNATTEMPTED', result_status = 'UNATTEMPTED',
                        completed_at = now(), error_message = 'omitted from returned QCC result'
                    WHERE task_id = %s
                    """,
                    (task_id,),
                )
                metrics["UNATTEMPTED"] += 1

            cur.execute(
                """
                UPDATE contact.import_run
                SET status = 'COMPLETED', metrics = %s::jsonb, finished_at = now()
                WHERE run_id = %s
                """,
                (json.dumps(metrics), import_run_id),
            )
            cur.execute(
                """
                UPDATE acquisition.cn_qcc_batch
                SET status = 'COMPLETED', result_received_at = now(), completed_at = now(),
                    result_path = %s, result_sha256 = %s, metrics = metrics || %s::jsonb
                WHERE batch_id = %s
                """,
                (str(result_path), result_sha256, json.dumps({"result_status_counts": metrics}), batch_id),
            )
            cur.execute(
                """
                UPDATE acquisition.cn_qcc_planner_state
                SET source_rank_watermark = %s,
                    source_entity_watermark = %s,
                    backfill_bucket = %s,
                    last_completed_batch_id = %s,
                    updated_at = now()
                WHERE state_key = 'CN_QCC_APPLICANT'
                """,
                (
                    int(batch["source_rank_to"]),
                    str(batch["source_entity_to"] or ""),
                    (int(batch["backfill_bucket"]) + 1) % 52,
                    batch["batch_id"],
                ),
            )
        conn.commit()
    return {
        "batch_id": batch_id,
        "status": "COMPLETED",
        "result_sha256": result_sha256,
        "result_status_counts": metrics,
        "task_count": len(tasks),
    }


__all__ = ["ingest_result"]
