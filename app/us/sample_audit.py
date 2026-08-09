from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import BinaryIO, Iterator
import zipfile

from app.us.parser import iter_case_bundles


SAMPLE_AUDIT_VERSION = "US_APPLICATION_SAMPLE_AUDIT_M1.0"
SOURCE_KINDS = {"HISTORICAL", "DAILY"}
RAW_STORAGE_POLICY = "KEEP_OFFICIAL_ZIP_COMPRESSED_STREAM_XML_MEMBERS_NO_PERSISTENT_EXTRACTION"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_info(path: Path) -> tuple[list[str], int, bool]:
    if path.suffix.lower() == ".xml":
        return [path.name], path.stat().st_size, False
    if path.suffix.lower() != ".zip":
        raise RuntimeError(f"Unsupported USPTO application sample type: {path.name}")
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            item
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".xml")
        )
        if not members:
            raise RuntimeError(f"USPTO application ZIP contains no XML member: {path.name}")
        return [item.filename for item in members], sum(item.file_size for item in members), True


def _sources(path: Path) -> Iterator[tuple[str, BinaryIO | Path]]:
    if path.suffix.lower() == ".xml":
        yield path.name, path
        return
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            item
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".xml")
        )
        for member in members:
            # XML is decompressed only through this stream. No extracted XML file is written.
            with archive.open(member, "r") as stream:
                yield member.filename, stream


def _present(counter: Counter[str], key: str, value: object) -> None:
    if value not in (None, "", (), False, 0):
        counter[key] += 1


def audit_application_sample(
    path: Path,
    *,
    source_kind: str,
    effective_date: date | None = None,
) -> dict[str, object]:
    source_kind = source_kind.strip().upper()
    if source_kind not in SOURCE_KINDS:
        raise ValueError("source_kind must be HISTORICAL or DAILY")
    path = path.resolve()
    result: dict[str, object] = {
        "audit_version": SAMPLE_AUDIT_VERSION,
        "status": "FAIL",
        "source": {
            "path": str(path),
            "file_name": path.name,
            "source_kind": source_kind,
            "effective_date": effective_date.isoformat() if effective_date else None,
            "effective_date_inferred_from_filename": False,
        },
        "raw_storage_policy": RAW_STORAGE_POLICY,
        "scale_up_authorized": False,
    }
    try:
        if not path.is_file():
            raise RuntimeError(f"USPTO application sample not found: {path}")
        xml_members, uncompressed_xml_bytes, streamed_from_zip = _source_info(path)
        source = result["source"]
        assert isinstance(source, dict)
        source.update(
            {
                "sha256": _sha256(path),
                "compressed_or_file_bytes": path.stat().st_size,
                "uncompressed_xml_bytes": uncompressed_xml_bytes,
                "xml_members": xml_members,
                "streamed_from_zip": streamed_from_zip,
                "persistent_xml_extraction": False,
            }
        )

        counts: Counter[str] = Counter()
        case_fields: Counter[str] = Counter()
        owner_fields: Counter[str] = Counter()
        classification_fields: Counter[str] = Counter()
        transaction_dates: set[str] = set()
        serials: set[str] = set()
        duplicate_serials: list[str] = []
        examples: list[str] = []
        warnings: list[str] = []

        for member_name, stream in _sources(path):
            member_cases = 0
            for bundle in iter_case_bundles(stream, source_name=member_name):
                member_cases += 1
                counts["cases"] += 1
                case = bundle.case
                if case.serial_number in serials:
                    if len(duplicate_serials) < 20:
                        duplicate_serials.append(case.serial_number)
                else:
                    serials.add(case.serial_number)
                if len(examples) < 10:
                    examples.append(case.serial_number)
                if case.transaction_date:
                    transaction_dates.add(case.transaction_date.isoformat())

                for key in (
                    "registration_number",
                    "transaction_date",
                    "filing_date",
                    "publication_date",
                    "registration_date",
                    "abandonment_date",
                    "cancellation_date",
                    "renewal_date",
                    "status_code",
                    "status_date",
                    "mark_identification",
                    "mark_drawing_code",
                    "current_location",
                    "location_date",
                    "examiner_name",
                    "law_office_code",
                    "international_registration_number",
                    "international_registration_status_code",
                ):
                    _present(case_fields, key, getattr(case, key))
                for key in (
                    "use_1a_current",
                    "intent_to_use_1b_current",
                    "foreign_application_44d_current",
                    "foreign_registration_44e_current",
                    "madrid_66a_current",
                    "no_basis_current",
                    "renewal_filed",
                    "section_8_filed",
                    "section_8_accepted",
                    "section_15_filed",
                    "section_15_acknowledged",
                    "opposition_pending",
                    "cancellation_pending",
                ):
                    if getattr(case, key):
                        case_fields[key] += 1

                counts["owners"] += len(bundle.owners)
                counts["classifications"] += len(bundle.classifications)
                counts["events"] += len(bundle.events)
                counts["statements"] += len(bundle.statements)
                counts["correspondents"] += int(bundle.correspondent is not None)
                counts["design_searches"] += len(bundle.design_searches)
                counts["prior_registrations"] += len(bundle.prior_registrations)
                counts["foreign_applications"] += len(bundle.foreign_applications)
                counts["madrid_filings"] += len(bundle.madrid_filings)
                counts["madrid_events"] += len(bundle.madrid_events)

                for owner in bundle.owners:
                    for key in (
                        "party_name",
                        "party_type",
                        "legal_entity_type_code",
                        "entity_statement",
                        "nationality_country",
                        "nationality_state",
                        "country",
                        "state",
                        "city",
                        "postcode",
                    ):
                        _present(owner_fields, key, getattr(owner, key))
                for classification in bundle.classifications:
                    for key in (
                        "primary_code",
                        "international_codes",
                        "us_codes",
                        "status_code",
                        "status_date",
                        "first_use_anywhere_raw",
                        "first_use_commerce_raw",
                    ):
                        _present(classification_fields, key, getattr(classification, key))

            if member_cases == 0:
                warnings.append(f"XML member produced zero trademark cases: {member_name}")

        if counts["cases"] == 0:
            raise RuntimeError("USPTO application sample produced zero trademark cases")
        if duplicate_serials:
            raise RuntimeError(
                "Duplicate serial number(s) in one application sample: "
                + ", ".join(duplicate_serials)
            )
        if not counts["owners"]:
            warnings.append("sample_contains_no_owner_records")
        if not counts["classifications"]:
            warnings.append("sample_contains_no_classification_records")

        result.update(
            {
                "status": "PASS_WITH_WARNINGS" if warnings else "PASS",
                "counts": dict(sorted(counts.items())),
                "parsed_field_coverage": {
                    "case": dict(sorted(case_fields.items())),
                    "owner": dict(sorted(owner_fields.items())),
                    "classification": dict(sorted(classification_fields.items())),
                },
                "transaction_dates": sorted(transaction_dates),
                "examples": {"serial_numbers": examples},
                "warnings": warnings,
                "gate": {
                    "parser_completed": True,
                    "case_records_present": True,
                    "zip_streaming_verified": streamed_from_zip,
                    "ready_for_sample_ingest": not warnings,
                    "scale_up_authorized": False,
                },
            }
        )
    except Exception as exc:
        result.update(
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "gate": {
                    "parser_completed": False,
                    "case_records_present": False,
                    "zip_streaming_verified": False,
                    "ready_for_sample_ingest": False,
                    "scale_up_authorized": False,
                },
            }
        )
    return result


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit for one USPTO Trademark Application XML/ZIP sample."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--source-kind", required=True, choices=sorted(SOURCE_KINDS))
    parser.add_argument("--effective-date", help="Explicit YYYY-MM-DD; never inferred from filename")
    args = parser.parse_args()
    report = audit_application_sample(
        args.source,
        source_kind=args.source_kind,
        effective_date=_parse_date(args.effective_date),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] in {"PASS", "PASS_WITH_WARNINGS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
