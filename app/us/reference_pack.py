from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path
import re
from typing import Any

from app.us.event_reference import (
    AUTHORITY as EVENT_AUTHORITY,
    REFERENCE_KIND as EVENT_REFERENCE_KIND,
    REFERENCE_PAYLOAD_SCHEMA as EVENT_SCHEMA,
    normalize_reference_payload as normalize_event_payload,
)
from app.us.reference_evidence import sha256_file, verify_payload_source_file
from app.us.status_reference import (
    AUTHORITY as STATUS_AUTHORITY,
    REFERENCE_KIND as STATUS_REFERENCE_KIND,
    REFERENCE_PAYLOAD_SCHEMA as STATUS_SCHEMA,
    normalize_reference_payload as normalize_status_payload,
)


PACK_MANIFEST_SCHEMA = "MARKORBIT_USPTO_REFERENCE_PACK_MANIFEST_V1"
_ALLOWED_FAMILIES = {"status", "event"}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_version(value: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", value.strip()).strip("._")
    if not cleaned:
        raise ValueError("reference_version cannot produce an empty file-safe name")
    return cleaned


def _read_reviewed_csv(csv_path: Path) -> list[dict[str, str]]:
    try:
        handle = csv_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"Unable to read reviewed CSV {csv_path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        fieldnames = {str(name or "").strip() for name in (reader.fieldnames or [])}
        required = {"code", "official_description"}
        missing = sorted(required - fieldnames)
        if missing:
            raise ValueError(
                "Reviewed CSV is missing required columns: " + ", ".join(missing)
            )
        rows: list[dict[str, str]] = []
        for line_number, raw in enumerate(reader, start=2):
            code = str(raw.get("code") or "").strip()
            description = " ".join(
                str(raw.get("official_description") or "").strip().split()
            )
            if not code and not description:
                continue
            if not code or not description:
                raise ValueError(
                    f"Reviewed CSV line {line_number} requires code and official_description"
                )
            rows.append(
                {
                    "code": code,
                    "official_description": description,
                    "official_definition": str(
                        raw.get("official_definition") or ""
                    ).strip(),
                    "official_category": str(raw.get("official_category") or "").strip(),
                    "source_locator": str(raw.get("source_locator") or "").strip(),
                }
            )
    if not rows:
        raise ValueError("Reviewed CSV contains no reference rows")
    return rows


def build_reference_pack(
    *,
    family: str,
    source_document: Path,
    reviewed_csv: Path,
    reference_version: str,
    document_date: date,
    source_url: str,
    evidence_note: str = "",
    output_path: Path | None = None,
) -> dict[str, Any]:
    family = family.strip().lower()
    if family not in _ALLOWED_FAMILIES:
        raise ValueError("family must be 'status' or 'event'")
    if not source_document.is_file():
        raise ValueError(f"Official source document does not exist: {source_document}")
    if not reviewed_csv.is_file():
        raise ValueError(f"Reviewed CSV does not exist: {reviewed_csv}")
    if reviewed_csv.parent.resolve() != source_document.parent.resolve():
        raise ValueError(
            "Reviewed CSV must be retained beside the official source document so the evidence pack remains self-contained"
        )

    if output_path is None:
        output_path = source_document.with_name(
            f"{family}_reference_{_safe_version(reference_version)}.json"
        )
    if output_path.parent.resolve() != source_document.parent.resolve():
        raise ValueError(
            "Reference payload must be written beside the official source document so production import can re-hash the source evidence"
        )

    records = _read_reviewed_csv(reviewed_csv)
    source_sha = sha256_file(source_document)
    csv_sha = sha256_file(reviewed_csv)

    if family == "status":
        payload = {
            "schema": STATUS_SCHEMA,
            "authority": STATUS_AUTHORITY,
            "reference_kind": STATUS_REFERENCE_KIND,
            "reference_version": reference_version,
            "source": {
                "document_name": source_document.name,
                "document_date": document_date.isoformat(),
                "url": source_url,
                "sha256": source_sha,
                "evidence_note": evidence_note,
            },
            "records": records,
        }
        normalized = normalize_status_payload(payload)
    else:
        payload = {
            "schema": EVENT_SCHEMA,
            "authority": EVENT_AUTHORITY,
            "reference_kind": EVENT_REFERENCE_KIND,
            "reference_version": reference_version,
            "source": {
                "document_name": source_document.name,
                "document_date": document_date.isoformat(),
                "url": source_url,
                "sha256": source_sha,
                "evidence_note": evidence_note,
            },
            "records": records,
        }
        normalized = normalize_event_payload(payload)

    output_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    verified_source = verify_payload_source_file(normalized, output_path)

    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest = {
        "schema": PACK_MANIFEST_SCHEMA,
        "family": family,
        "reference_version": normalized["reference_version"],
        "source_document": source_document.name,
        "source_document_sha256": source_sha,
        "reviewed_transcription_csv": reviewed_csv.name,
        "reviewed_transcription_sha256": csv_sha,
        "payload_file": output_path.name,
        "normalized_payload_sha256": normalized["normalized_payload_sha256"],
        "record_count": len(normalized["records"]),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "status": "BUILT",
        "payload_path": str(output_path),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "source_evidence": verified_source,
    }
