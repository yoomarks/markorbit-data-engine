from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


COLLECTOR_CONTRACT_VERSION = "US_TSDR_COLLECTOR_TXT_CSV_V1"
VISUAL_ASSET_POLICY_VERSION = "US_TSDR_MARK_IMAGE_DEFERRED_V1"
STATUS_VIEW_TEMPLATE = "https://tsdr.uspto.gov/statusview/sn{serial_number}"
MARK_IMAGE_TEMPLATE = "https://tsdr.uspto.gov/img/{serial_number}/large"
DEFAULT_MARK_IMAGE_MODE = "DEFERRED_REMOTE_ONLY"

_EMAIL_RE = re.compile(r"(?i)(?<![A-Z0-9._%+\-])[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}(?![A-Z0-9._%+\-])")
_SERIAL_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")


def validate_serial_number(value: object) -> str:
    serial = str(value or "").strip()
    if len(serial) != 8 or not serial.isdigit():
        raise ValueError(f"US TSDR serial_number must contain exactly 8 digits: {serial!r}")
    return serial


def status_view_url(serial_number: object) -> str:
    return STATUS_VIEW_TEMPLATE.format(serial_number=validate_serial_number(serial_number))


def mark_image_url(serial_number: object) -> str:
    return MARK_IMAGE_TEMPLATE.format(serial_number=validate_serial_number(serial_number))


def mark_image_descriptor(serial_number: object) -> dict[str, object]:
    """Return a remote locator without implying that the image must be downloaded.

    Mark-image binary acquisition is intentionally independent from structured TSDR
    coverage. Missing local image bytes are therefore not a structured-data gap.
    """
    serial = validate_serial_number(serial_number)
    return {
        "policy_version": VISUAL_ASSET_POLICY_VERSION,
        "asset_kind": "USPTO_TSDR_MARK_IMAGE",
        "serial_number": serial,
        "source_url": mark_image_url(serial),
        "acquisition_mode": DEFAULT_MARK_IMAGE_MODE,
        "binary_required_for_tsdr_structured_coverage": False,
    }


def _label_key(value: object) -> str:
    text = str(value or "").strip().casefold().replace("_", " ")
    text = re.sub(r"[\s/\-]+", " ", text)
    text = text.rstrip(":").strip()
    return text


_FIELD_ALIASES = {
    "serial number": "serial_number",
    "serial": "serial_number",
    "application serial number": "serial_number",
    "source url": "source_url",
    "url": "source_url",
    "tsdr url": "source_url",
    "attorney name": "attorney_name",
    "docket number": "docket_number",
    "attorney primary email address": "attorney_primary_email",
    "attorney email authorized": "attorney_email_authorized",
    "correspondent name address": "correspondent_name_address_raw",
    "correspondent name and address": "correspondent_name_address_raw",
    "phone": "phone",
    "correspondent phone": "phone",
    "correspondent e mail": "correspondent_email_raw",
    "correspondent email": "correspondent_email_raw",
    "correspondent e mail authorized": "correspondent_email_authorized",
    "correspondent email authorized": "correspondent_email_authorized",
    "fetched at": "collected_at",
    "collected at": "collected_at",
    "scraped at": "collected_at",
    "timestamp": "collected_at",
}


def _canonical_key(label: object) -> str | None:
    return _FIELD_ALIASES.get(_label_key(label))


def _bool(value: object) -> bool | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    if text in {"yes", "y", "true", "1", "authorized"}:
        return True
    if text in {"no", "n", "false", "0", "not authorized"}:
        return False
    return None


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serial_from_text(value: object) -> str | None:
    text = str(value or "")
    match = _SERIAL_RE.search(text)
    return match.group(1) if match else None


def _emails(value: object) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for match in _EMAIL_RE.findall(str(value or "")):
        normalized = match.strip().casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _lines(value: object) -> tuple[str, ...]:
    return tuple(line.strip() for line in str(value or "").splitlines() if line.strip())


@dataclass(frozen=True)
class CollectorObservation:
    serial_number: str
    source_url: str
    attorney_name: str
    docket_number: str
    attorney_primary_email: str
    attorney_email_authorized: bool | None
    correspondent_name_address_raw: str
    correspondent_name_address_lines: tuple[str, ...]
    phone: str
    correspondent_emails: tuple[str, ...]
    correspondent_email_authorized: bool | None
    collected_at: datetime | None
    raw_fields: dict[str, str]

    def normalized_payload(self) -> dict[str, object]:
        return {
            "serial_number": self.serial_number,
            "source_url": self.source_url,
            "attorney_name": self.attorney_name,
            "docket_number": self.docket_number,
            "attorney_primary_email": self.attorney_primary_email,
            "attorney_email_authorized": self.attorney_email_authorized,
            "correspondent_name_address_raw": self.correspondent_name_address_raw,
            "correspondent_name_address_lines": list(self.correspondent_name_address_lines),
            "phone": self.phone,
            "correspondent_emails": list(self.correspondent_emails),
            "correspondent_email_authorized": self.correspondent_email_authorized,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
            "raw_fields": dict(self.raw_fields),
        }


def _observation_from_fields(
    fields: dict[str, str],
    *,
    fallback_serial: str | None = None,
) -> CollectorObservation:
    serial = str(fields.get("serial_number") or "").strip()
    if serial:
        serial = _serial_from_text(serial) or serial
    if not serial:
        serial = _serial_from_text(fields.get("source_url")) or fallback_serial or ""
    serial = validate_serial_number(serial)

    source_url = str(fields.get("source_url") or "").strip() or status_view_url(serial)
    correspondent_raw = str(fields.get("correspondent_name_address_raw") or "").strip()
    return CollectorObservation(
        serial_number=serial,
        source_url=source_url,
        attorney_name=str(fields.get("attorney_name") or "").strip(),
        docket_number=str(fields.get("docket_number") or "").strip(),
        attorney_primary_email=str(fields.get("attorney_primary_email") or "").strip().casefold(),
        attorney_email_authorized=_bool(fields.get("attorney_email_authorized")),
        correspondent_name_address_raw=correspondent_raw,
        correspondent_name_address_lines=_lines(correspondent_raw),
        phone=str(fields.get("phone") or "").strip(),
        correspondent_emails=_emails(fields.get("correspondent_email_raw")),
        correspondent_email_authorized=_bool(fields.get("correspondent_email_authorized")),
        collected_at=_timestamp(fields.get("collected_at")),
        raw_fields=dict(fields),
    )


def _parse_key_value_rows(rows: list[list[str]], *, fallback_serial: str | None) -> list[CollectorObservation]:
    fields: dict[str, str] = {}
    last_key: str | None = None
    for row in rows:
        if not row or not any(cell.strip() for cell in row):
            continue
        label = row[0].strip()
        value = ""
        canonical = _canonical_key(label)
        if canonical is not None:
            value = ",".join(row[1:]).strip() if len(row) > 1 else ""
        elif len(row) == 1 and ":" in label:
            possible_label, possible_value = label.split(":", 1)
            canonical = _canonical_key(possible_label)
            if canonical is not None:
                value = possible_value.strip()
        if canonical is not None:
            if canonical in fields and fields[canonical] and value:
                fields[canonical] = fields[canonical] + "\n" + value
            elif value or canonical not in fields:
                fields[canonical] = value
            last_key = canonical
            continue

        # Preserve wrapped lines in Correspondent Name/Address instead of guessing
        # which token is a person, firm, street, city, state, or postal code.
        if last_key == "correspondent_name_address_raw":
            continuation = ",".join(row).strip()
            if continuation:
                fields[last_key] = (fields.get(last_key, "") + "\n" + continuation).strip()

    return [_observation_from_fields(fields, fallback_serial=fallback_serial)] if fields else []


def _parse_wide_rows(rows: list[list[str]], *, fallback_serial: str | None) -> list[CollectorObservation]:
    if not rows:
        return []
    headers = rows[0]
    mapped_headers = [_canonical_key(value) for value in headers]
    observations: list[CollectorObservation] = []
    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        fields: dict[str, str] = {}
        raw_fields: dict[str, str] = {}
        for index, header in enumerate(headers):
            value = row[index] if index < len(row) else ""
            raw_fields[str(header)] = value
            canonical = mapped_headers[index]
            if canonical is not None:
                fields[canonical] = value
        observation = _observation_from_fields(fields, fallback_serial=fallback_serial)
        combined = observation.normalized_payload()
        combined_raw = dict(observation.raw_fields)
        combined_raw.update(raw_fields)
        observations.append(
            CollectorObservation(
                serial_number=observation.serial_number,
                source_url=observation.source_url,
                attorney_name=observation.attorney_name,
                docket_number=observation.docket_number,
                attorney_primary_email=observation.attorney_primary_email,
                attorney_email_authorized=observation.attorney_email_authorized,
                correspondent_name_address_raw=observation.correspondent_name_address_raw,
                correspondent_name_address_lines=observation.correspondent_name_address_lines,
                phone=observation.phone,
                correspondent_emails=observation.correspondent_emails,
                correspondent_email_authorized=observation.correspondent_email_authorized,
                collected_at=observation.collected_at,
                raw_fields=combined_raw,
            )
        )
        del combined
    return observations


def parse_collector_csv(path: Path, *, serial_number: str | None = None) -> list[CollectorObservation]:
    """Parse either a wide CSV export or the collector's label/value CSV form.

    The parser intentionally preserves the entire Correspondent Name/Address block.
    It extracts only fields that are explicit in the collector output and does not
    infer whether a secondary correspondent email belongs to a US attorney, foreign
    intermediary, applicant, or another party.
    """
    path = Path(path)
    fallback_serial = validate_serial_number(serial_number) if serial_number else _serial_from_text(path.name)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [list(row) for row in csv.reader(handle)]
    if not rows:
        return []

    header_hits = sum(1 for cell in rows[0] if _canonical_key(cell) is not None)
    likely_wide = len(rows[0]) >= 3 and header_hits >= 2
    if likely_wide:
        return _parse_wide_rows(rows, fallback_serial=fallback_serial)
    return _parse_key_value_rows(rows, fallback_serial=fallback_serial)


def collector_task_lines(serial_numbers: Iterable[object]) -> list[str]:
    return [status_view_url(value) for value in serial_numbers]
