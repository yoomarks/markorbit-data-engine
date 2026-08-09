from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from app.db import postgres_conn


REFERENCE_PAYLOAD_SCHEMA = "MARKORBIT_USPTO_EVENT_REFERENCE_V1"
REFERENCE_KIND = "TRADEMARK_EVENT_CODES"
AUTHORITY = "USPTO"
CURRENT_OFFICIAL_DOCUMENT_NAME = "Trademark Applications Documentation v2.3-20250813.doc"
CURRENT_OFFICIAL_DOCUMENT_DATE = date(2025, 8, 13)
CURRENT_OFFICIAL_SOURCE_PAGE_URL = (
    "https://www.uspto.gov/trademarks/trademark-updates-and-announcements/"
    "xml-resources"
)

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _payload_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _validate_official_url(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host == "uspto.gov" or host.endswith(".uspto.gov")
    ):
        raise ValueError("source.url must be an HTTPS USPTO domain URL")
    return value


def normalize_reference_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != REFERENCE_PAYLOAD_SCHEMA:
        raise ValueError(f"schema must be {REFERENCE_PAYLOAD_SCHEMA}")
    if payload.get("authority") != AUTHORITY:
        raise ValueError("authority must be USPTO")
    if payload.get("reference_kind") != REFERENCE_KIND:
        raise ValueError(f"reference_kind must be {REFERENCE_KIND}")

    reference_version = _clean_text(payload.get("reference_version"))
    if not _VERSION_RE.fullmatch(reference_version):
        raise ValueError("reference_version has an invalid format")

    source = payload.get("source")
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    document_name = _clean_text(source.get("document_name"))
    if not document_name:
        raise ValueError("source.document_name is required")
    try:
        document_date = date.fromisoformat(_clean_text(source.get("document_date")))
    except ValueError as exc:
        raise ValueError("source.document_date must be YYYY-MM-DD") from exc
    source_url = _validate_official_url(_clean_text(source.get("url")))
    source_sha256 = _clean_text(source.get("sha256")).lower()
    if not _SHA_RE.fullmatch(source_sha256):
        raise ValueError("source.sha256 must be a 64-character hexadecimal SHA-256")
    evidence_note = _clean_text(source.get("evidence_note"))

    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("records must be a non-empty array")

    records: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for index, raw in enumerate(raw_records, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"records[{index}] must be an object")
        code = _clean_text(raw.get("code")).upper()
        if not _CODE_RE.fullmatch(code):
            raise ValueError(
                f"records[{index}].code must be uppercase alphanumeric/event-safe"
            )
        if code in seen_codes:
            raise ValueError(f"duplicate event code in payload: {code}")
        seen_codes.add(code)
        description = _clean_text(raw.get("official_description"))
        if not description:
            raise ValueError(
                f"records[{index}].official_description must be non-empty"
            )
        records.append(
            {
                "code": code,
                "official_description": description,
                "official_definition": _clean_text(raw.get("official_definition")),
                "official_category": _clean_text(raw.get("official_category")),
                "source_locator": _clean_text(raw.get("source_locator")),
            }
        )

    records.sort(key=lambda item: item["code"])
    normalized = {
        "schema": REFERENCE_PAYLOAD_SCHEMA,
        "authority": AUTHORITY,
        "reference_kind": REFERENCE_KIND,
        "reference_version": reference_version,
        "source": {
            "document_name": document_name,
            "document_date": document_date.isoformat(),
            "url": source_url,
            "sha256": source_sha256,
            "evidence_note": evidence_note,
        },
        "records": records,
    }
    normalized["normalized_payload_sha256"] = _payload_sha256(normalized)
    return normalized


def load_reference_payload(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read reference payload {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("reference payload root must be an object")
    return normalize_reference_payload(parsed)


def event_reference_schema_ready() -> bool:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    to_regclass('reference.us_trademark_event_reference_version') IS NOT NULL
                    AND to_regclass('reference.us_trademark_event_code') IS NOT NULL AS ready
                """
            )
            row = cur.fetchone()
            return bool(row and row["ready"])


def import_reference_payload(
    payload: dict[str, Any],
    *,
    activate: bool = True,
) -> dict[str, Any]:
    normalized = normalize_reference_payload(payload)
    version = normalized["reference_version"]
    source = normalized["source"]
    records = normalized["records"]
    normalized_sha = normalized["normalized_payload_sha256"]

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext('markorbit:us:event-reference-import'))"
            )
            cur.execute(
                """
                SELECT
                    to_regclass('reference.us_trademark_event_reference_version') IS NOT NULL
                    AND to_regclass('reference.us_trademark_event_code') IS NOT NULL AS ready
                """
            )
            schema_row = cur.fetchone()
            if not schema_row or not schema_row["ready"]:
                raise RuntimeError(
                    "USPTO event reference schema is not ready; apply US schema first"
                )

            cur.execute(
                """
                SELECT reference_version, source_document_sha256,
                       normalized_payload_sha256, record_count, is_active
                FROM reference.us_trademark_event_reference_version
                WHERE reference_version = %s
                """,
                (version,),
            )
            existing = cur.fetchone()
            if existing:
                if (
                    str(existing["source_document_sha256"]).lower()
                    != source["sha256"]
                    or str(existing["normalized_payload_sha256"]).lower()
                    != normalized_sha
                    or int(existing["record_count"]) != len(records)
                ):
                    raise RuntimeError(
                        "Reference version already exists with different source/payload evidence: "
                        f"{version}"
                    )
                action = "ALREADY_IMPORTED"
                if activate and not bool(existing["is_active"]):
                    cur.execute(
                        """
                        UPDATE reference.us_trademark_event_reference_version
                        SET is_active = false
                        WHERE is_active = true AND reference_version <> %s
                        """,
                        (version,),
                    )
                    cur.execute(
                        """
                        UPDATE reference.us_trademark_event_reference_version
                        SET is_active = true
                        WHERE reference_version = %s
                        """,
                        (version,),
                    )
                    action = "ACTIVATED_EXISTING"
                conn.commit()
                return {
                    "status": action,
                    "reference_version": version,
                    "record_count": len(records),
                    "source_document_sha256": source["sha256"],
                    "normalized_payload_sha256": normalized_sha,
                    "active": activate or bool(existing["is_active"]),
                }

            cur.execute(
                """
                INSERT INTO reference.us_trademark_event_reference_version (
                    reference_version, authority, reference_kind,
                    source_document_name, source_document_date, source_url,
                    source_document_sha256, normalized_payload_sha256,
                    record_count, is_active, evidence_note
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s)
                """,
                (
                    version,
                    AUTHORITY,
                    REFERENCE_KIND,
                    source["document_name"],
                    source["document_date"],
                    source["url"],
                    source["sha256"],
                    normalized_sha,
                    len(records),
                    source["evidence_note"],
                ),
            )
            for record in records:
                cur.execute(
                    """
                    INSERT INTO reference.us_trademark_event_code (
                        reference_version, raw_code, official_description,
                        official_definition, official_category, source_locator
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        version,
                        record["code"],
                        record["official_description"],
                        record["official_definition"],
                        record["official_category"],
                        record["source_locator"],
                    ),
                )
            if activate:
                cur.execute(
                    """
                    UPDATE reference.us_trademark_event_reference_version
                    SET is_active = false
                    WHERE is_active = true AND reference_version <> %s
                    """,
                    (version,),
                )
                cur.execute(
                    """
                    UPDATE reference.us_trademark_event_reference_version
                    SET is_active = true
                    WHERE reference_version = %s
                    """,
                    (version,),
                )
        conn.commit()

    return {
        "status": "IMPORTED",
        "reference_version": version,
        "record_count": len(records),
        "source_document_sha256": source["sha256"],
        "normalized_payload_sha256": normalized_sha,
        "active": activate,
    }


def active_reference_metadata() -> dict[str, Any] | None:
    if not event_reference_schema_ready():
        return None
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT reference_version, authority, reference_kind,
                       source_document_name, source_document_date, source_url,
                       source_document_sha256, normalized_payload_sha256,
                       record_count, imported_at, evidence_note
                FROM reference.us_trademark_event_reference_version
                WHERE is_active = true
                LIMIT 1
                """
            )
            row = cur.fetchone()
            return dict(row) if row else None


def list_active_event_codes() -> dict[str, Any]:
    metadata = active_reference_metadata()
    if metadata is None:
        return {"reference": None, "event_codes": []}
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT raw_code, official_description, official_definition,
                       official_category, source_locator
                FROM reference.us_trademark_event_code
                WHERE reference_version = %s
                ORDER BY raw_code
                """,
                (metadata["reference_version"],),
            )
            rows = [dict(row) for row in cur.fetchall()]
    return {"reference": metadata, "event_codes": rows}


def lookup_active_event_codes(
    codes: list[str] | tuple[str, ...] | set[str],
) -> dict[str, Any]:
    unique_codes = sorted({_clean_text(code).upper() for code in codes if _clean_text(code)})
    metadata = active_reference_metadata()
    if metadata is None or not unique_codes:
        return {"reference": metadata, "mappings": {}}

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT raw_code, official_description, official_definition,
                       official_category, source_locator
                FROM reference.us_trademark_event_code
                WHERE reference_version = %s
                  AND raw_code = ANY(%s)
                ORDER BY raw_code
                """,
                (metadata["reference_version"], unique_codes),
            )
            mappings = {str(row["raw_code"]): dict(row) for row in cur.fetchall()}
    return {"reference": metadata, "mappings": mappings}
