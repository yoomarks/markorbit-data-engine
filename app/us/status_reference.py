from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from app.db import postgres_conn


REFERENCE_PAYLOAD_SCHEMA = "MARKORBIT_USPTO_STATUS_REFERENCE_V1"
REFERENCE_KIND = "TRADEMARK_STATUS_CODES"
AUTHORITY = "USPTO"
CURRENT_OFFICIAL_DOCUMENT_NAME = "Table1TrademarkStatusCodes_20250813.doc"
CURRENT_OFFICIAL_DOCUMENT_DATE = date(2025, 8, 13)
CURRENT_OFFICIAL_DOCUMENT_URL = (
    "https://data.uspto.gov/ui/datasets/products/files/TRTDXFAP/"
    "Table1TrademarkStatusCodes_20250813.doc"
)
CURRENT_XML_RESOURCES_URL = (
    "https://www.uspto.gov/trademarks/trademark-updates-and-announcements/"
    "xml-resources"
)

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_CODE_RE = re.compile(r"^[0-9]{1,10}$")
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
        code = _clean_text(raw.get("code"))
        if not _CODE_RE.fullmatch(code):
            raise ValueError(f"records[{index}].code must contain digits only")
        if code in seen_codes:
            raise ValueError(f"duplicate status code in payload: {code}")
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

    records.sort(key=lambda item: (int(item["code"]), item["code"]))
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


def status_reference_schema_ready() -> bool:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    to_regclass('reference.us_trademark_status_reference_version') IS NOT NULL
                    AND to_regclass('reference.us_trademark_status_code') IS NOT NULL AS ready
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
                "SELECT pg_advisory_xact_lock(hashtext('markorbit:us:status-reference-import'))"
            )
            cur.execute(
                """
                SELECT
                    to_regclass('reference.us_trademark_status_reference_version') IS NOT NULL
                    AND to_regclass('reference.us_trademark_status_code') IS NOT NULL AS ready
                """
            )
            schema_row = cur.fetchone()
            if not schema_row or not schema_row["ready"]:
                raise RuntimeError(
                    "USPTO status reference schema is not ready; apply US schema first"
                )

            cur.execute(
                """
                SELECT reference_version, source_document_sha256,
                       normalized_payload_sha256, record_count, is_active
                FROM reference.us_trademark_status_reference_version
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
                        UPDATE reference.us_trademark_status_reference_version
                        SET is_active = false
                        WHERE is_active = true AND reference_version <> %s
                        """,
                        (version,),
                    )
                    cur.execute(
                        """
                        UPDATE reference.us_trademark_status_reference_version
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
                INSERT INTO reference.us_trademark_status_reference_version (
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
                    INSERT INTO reference.us_trademark_status_code (
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
            cur.execute(
                """
                SELECT count(*) AS count
                FROM reference.us_trademark_status_code
                WHERE reference_version = %s
                """,
                (version,),
            )
            actual_count = int(cur.fetchone()["count"])
            if actual_count != len(records):
                raise RuntimeError(
                    f"Reference import row-count mismatch: expected={len(records)} actual={actual_count}"
                )
            if activate:
                cur.execute(
                    """
                    UPDATE reference.us_trademark_status_reference_version
                    SET is_active = false
                    WHERE is_active = true AND reference_version <> %s
                    """,
                    (version,),
                )
                cur.execute(
                    """
                    UPDATE reference.us_trademark_status_reference_version
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
    if not status_reference_schema_ready():
        return None
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT reference_version, authority, reference_kind,
                       source_document_name, source_document_date, source_url,
                       source_document_sha256, normalized_payload_sha256,
                       record_count, imported_at, evidence_note
                FROM reference.us_trademark_status_reference_version
                WHERE is_active = true
                LIMIT 1
                """
            )
            row = cur.fetchone()
            return dict(row) if row else None


def lookup_active_status_codes(codes: list[str] | tuple[str, ...] | set[str]) -> dict[str, Any]:
    unique_codes = sorted({_clean_text(code) for code in codes if _clean_text(code)})
    metadata = active_reference_metadata()
    if metadata is None or not unique_codes:
        return {"reference": metadata, "mappings": {}}

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT raw_code, official_description, official_definition,
                       official_category, source_locator
                FROM reference.us_trademark_status_code
                WHERE reference_version = %s
                  AND raw_code = ANY(%s)
                ORDER BY raw_code
                """,
                (metadata["reference_version"], unique_codes),
            )
            mappings = {str(row["raw_code"]): dict(row) for row in cur.fetchall()}
    return {"reference": metadata, "mappings": mappings}


def enrich_status_counts(status_counts: list[dict[str, Any]]) -> dict[str, Any]:
    codes = [str(row.get("status_code") or "") for row in status_counts]
    lookup = lookup_active_status_codes(codes)
    mappings = lookup["mappings"]
    enriched: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    for row in status_counts:
        code = str(row.get("status_code") or "")
        mapping = mappings.get(code)
        item = {**row, "official_status_reference": mapping}
        enriched.append(item)
        if code and mapping is None:
            unmapped.append(dict(row))
    return {
        "reference": lookup["reference"],
        "status_codes": enriched,
        "unmapped_status_codes": unmapped,
        "mapped_code_count": sum(1 for row in enriched if row["official_status_reference"]),
        "unmapped_code_count": len(unmapped),
        "semantics": "USPTO_OFFICIAL_REFERENCE_NOT_MARKORBIT_LEGAL_CONCLUSION",
    }
