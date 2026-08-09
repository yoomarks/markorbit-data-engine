from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import BinaryIO, Iterator
import zipfile

from app.us_assignment.parser import iter_assignment_bundles


SAMPLE_AUDIT_VERSION = "US_ASSIGNMENT_SAMPLE_AUDIT_M1.1"
SOURCE_KINDS = {"HISTORICAL", "DAILY"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _xml_members(path: Path) -> list[str]:
    if path.suffix.lower() == ".xml":
        return [path.name]
    if path.suffix.lower() != ".zip":
        raise RuntimeError(f"Unsupported Assignment sample type: {path.name}")
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            item.filename
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".xml")
        )
    if not members:
        raise RuntimeError(f"Assignment ZIP contains no XML member: {path.name}")
    return members


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
            with archive.open(member, "r") as stream:
                yield member.filename, stream


def _present(counter: Counter[str], key: str, value: object) -> None:
    if value not in (None, ""):
        counter[key] += 1


def audit_assignment_sample(
    path: Path,
    *,
    source_kind: str,
    effective_date: date | None = None,
) -> dict[str, object]:
    source_kind = source_kind.upper().strip()
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
        "semantics": "USPTO_RECORDED_ASSIGNMENT_FACTS_NOT_LEGAL_TITLE_CONCLUSION",
        "legal_ownership_conclusion": False,
    }
    try:
        if not path.is_file():
            raise RuntimeError(f"Assignment sample not found: {path}")
        source = result["source"]
        assert isinstance(source, dict)
        source["sha256"] = _sha256(path)
        source["size_bytes"] = path.stat().st_size
        source["xml_members"] = _xml_members(path)

        counts: Counter[str] = Counter()
        record_fields: Counter[str] = Counter()
        party_fields: Counter[str] = Counter()
        property_fields: Counter[str] = Counter()
        warnings: list[str] = []
        examples: dict[str, list[str]] = {
            "reel_frame": [],
            "malformed_property_serial": [],
        }

        for member_name, stream in _sources(path):
            member_count = 0
            for bundle in iter_assignment_bundles(stream):
                member_count += 1
                counts["assignments"] += 1
                record = bundle.assignment
                if len(examples["reel_frame"]) < 10:
                    examples["reel_frame"].append(record.reel_frame_id)
                for key in (
                    "recorded_date_raw",
                    "last_update_date_raw",
                    "page_count",
                    "conveyance_text",
                    "purge_indicator",
                    "correspondent_name",
                    "correspondent_address_1",
                    "correspondent_address_2",
                    "correspondent_address_3",
                    "correspondent_address_4",
                ):
                    _present(record_fields, key, getattr(record, key))
                if record.recorded_date_raw and record.recorded_date is None:
                    counts["invalid_record_dates"] += 1
                if record.last_update_date_raw and record.last_update_date is None:
                    counts["invalid_record_dates"] += 1

                for party in (*bundle.assignors, *bundle.assignees):
                    counts["parties"] += 1
                    for key in (
                        "name",
                        "address_1",
                        "address_2",
                        "city",
                        "state",
                        "postcode",
                        "country",
                        "nationality",
                        "legal_entity_text",
                        "formerly_statement",
                        "composed_of_statement",
                        "dba_statement",
                        "execution_date_raw",
                        "acknowledgement_date_raw",
                    ):
                        _present(party_fields, key, getattr(party, key))
                    if not party.name:
                        counts["parties_without_name"] += 1
                    if party.execution_date_raw and party.execution_date is None:
                        counts["invalid_party_dates"] += 1
                    if party.acknowledgement_date_raw and party.acknowledgement_date is None:
                        counts["invalid_party_dates"] += 1
                counts["assignors"] += len(bundle.assignors)
                counts["assignees"] += len(bundle.assignees)

                for item in bundle.properties:
                    counts["properties"] += 1
                    identifiers = (
                        item.serial_number,
                        item.registration_number,
                        item.international_registration_number,
                    )
                    if not any(identifiers):
                        counts["properties_without_identifier"] += 1
                    _present(property_fields, "serial_number", item.serial_number)
                    _present(property_fields, "registration_number", item.registration_number)
                    _present(
                        property_fields,
                        "international_registration_number",
                        item.international_registration_number,
                    )
                    if item.serial_number and (
                        len(item.serial_number) != 8 or not item.serial_number.isdigit()
                    ):
                        counts["malformed_property_serials"] += 1
                        samples = examples["malformed_property_serial"]
                        if len(samples) < 10 and item.serial_number not in samples:
                            samples.append(item.serial_number)
            if member_count == 0:
                warnings.append(f"XML member produced zero assignments: {member_name}")

        if counts["assignments"] == 0:
            raise RuntimeError("Assignment sample produced zero assignment records")
        if counts["properties"] == 0:
            warnings.append("Sample contains no parsed trademark properties")
        if counts["parties_without_name"]:
            warnings.append("Sample contains parsed parties without names")
        if counts["properties_without_identifier"]:
            warnings.append("Sample contains properties without parsed identifiers")
        if counts["invalid_record_dates"] or counts["invalid_party_dates"]:
            warnings.append("Sample contains date text that could not be parsed as YYYYMMDD")
        if counts["malformed_property_serials"]:
            warnings.append("Sample contains non-8-digit property serial numbers")

        result.update(
            {
                "status": "PASS_WITH_WARNINGS" if warnings else "PASS",
                "counts": dict(sorted(counts.items())),
                "parsed_field_coverage": {
                    "record": dict(sorted(record_fields.items())),
                    "party": dict(sorted(party_fields.items())),
                    "property": dict(sorted(property_fields.items())),
                },
                "examples": examples,
                "warnings": warnings,
                "gate": {
                    "parser_completed": True,
                    "assignment_records_present": True,
                    "ready_for_sample_ingest": not warnings,
                    "scale_up_authorized": False,
                },
            }
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
        result["gate"] = {
            "parser_completed": False,
            "assignment_records_present": False,
            "ready_for_sample_ingest": False,
            "scale_up_authorized": False,
        }
    return result


def _parse_iso_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit one real USPTO Trademark Assignment XML/ZIP sample before ingest."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--source-kind", required=True, choices=sorted(SOURCE_KINDS))
    parser.add_argument("--effective-date", help="Explicit YYYY-MM-DD; never inferred from filename")
    args = parser.parse_args()
    report = audit_assignment_sample(
        args.source,
        source_kind=args.source_kind,
        effective_date=_parse_iso_date(args.effective_date),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] in {"PASS", "PASS_WITH_WARNINGS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
