from __future__ import annotations

import csv
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from app.global_trademarks.ca_st96 import iter_cipo_records
from app.global_trademarks.gb_open_data import UK_FIELDS, iter_ukipo_2018


_TM_LINK_REQUIRED: dict[str, tuple[str, ...]] = {
    "applications": ("application_number", "application_country", "filing_date", "registration_date"),
    "applicants": ("application_number", "application_country", "applicant_name"),
    "details": ("application_number", "application_country", "trademark_text"),
    "classes": ("application_number", "application_country", "nice_class"),
}

_AU_REQUIRED: dict[str, tuple[str, ...]] = {
    "application": ("ip_right_type", "application_number", "status"),
    "party-activity": ("ip_right_type", "application_number", "party_id", "party_role"),
    "application-links": (
        "ip_right_type",
        "application_number",
        "link_type",
        "linked_application_number",
    ),
    "application-events": ("ip_right_type", "application_number", "event_type"),
    "application-classification": ("ip_right_type", "application_number", "classification"),
    "application-description": (
        "ip_right_type",
        "application_number",
        "description_type",
        "description_value",
    ),
}


@dataclass(frozen=True, slots=True)
class SourcePreflight:
    source_kind: str
    path: str
    size_bytes: int
    sha256: str
    schema_valid: bool
    missing_columns: tuple[str, ...]
    sampled_rows: int
    usable_rows: int
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_header(path: Path, *, delimiter: str = ",") -> tuple[str, ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            return tuple(next(reader))
        except StopIteration:
            return ()


def _sample_csv_rows(path: Path, *, limit: int, delimiter: str = ",") -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        for index, row in enumerate(reader):
            if index >= limit:
                break
            yield row


def _result(
    *,
    source_kind: str,
    path: Path,
    missing_columns: tuple[str, ...] = (),
    sampled_rows: int,
    usable_rows: int,
    warnings: tuple[str, ...] = (),
) -> SourcePreflight:
    return SourcePreflight(
        source_kind=source_kind,
        path=str(path),
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
        schema_valid=not missing_columns,
        missing_columns=missing_columns,
        sampled_rows=sampled_rows,
        usable_rows=usable_rows,
        warnings=warnings,
    )


def inspect_gb_2018(path: Path, *, sample_limit: int = 100) -> SourcePreflight:
    header = _csv_header(path, delimiter="|")
    missing = tuple(field for field in UK_FIELDS if field not in header)
    if missing:
        return _result(
            source_kind="GB_2018",
            path=path,
            missing_columns=missing,
            sampled_rows=0,
            usable_rows=0,
        )

    sampled = usable = 0
    for record in iter_ukipo_2018(path):
        sampled += 1
        if record.get("application_number"):
            usable += 1
        if sampled >= sample_limit:
            break
    warnings = () if usable else ("no usable trademark records found in sample",)
    return _result(
        source_kind="GB_2018",
        path=path,
        sampled_rows=sampled,
        usable_rows=usable,
        warnings=warnings,
    )


def inspect_tm_link(
    path: Path,
    *,
    jurisdiction: str,
    table: str,
    sample_limit: int = 100,
) -> SourcePreflight:
    key = jurisdiction.strip().upper()
    if key == "EM":
        key = "EU"
    if key not in {"EU", "NZ"}:
        raise ValueError("TM-Link preflight supports only EU and NZ")
    if table not in _TM_LINK_REQUIRED:
        raise ValueError(f"unsupported TM-Link table: {table}")

    header = _csv_header(path)
    required = _TM_LINK_REQUIRED[table]
    missing = tuple(field for field in required if field not in header)
    if missing:
        return _result(
            source_kind=f"TM_LINK_{key}_{table}",
            path=path,
            missing_columns=missing,
            sampled_rows=0,
            usable_rows=0,
        )

    allowed_offices = {"EU", "EM"} if key == "EU" else {"NZ"}
    sampled = usable = 0
    for row in _sample_csv_rows(path, limit=sample_limit):
        sampled += 1
        office = (row.get("application_country") or "").strip().upper()
        application_number = (row.get("application_number") or "").strip()
        if office in allowed_offices and application_number:
            usable += 1

    warnings: list[str] = []
    if sampled and not usable:
        warnings.append(f"sample contains no {key} rows with application_number")
    return _result(
        source_kind=f"TM_LINK_{key}_{table}",
        path=path,
        sampled_rows=sampled,
        usable_rows=usable,
        warnings=tuple(warnings),
    )


def inspect_au_ipgod(path: Path, *, table: str, sample_limit: int = 100) -> SourcePreflight:
    if table not in _AU_REQUIRED:
        raise ValueError(f"unsupported IPGOD table: {table}")
    header = _csv_header(path)
    missing = tuple(field for field in _AU_REQUIRED[table] if field not in header)
    if missing:
        return _result(
            source_kind=f"AU_IPGOD_{table}",
            path=path,
            missing_columns=missing,
            sampled_rows=0,
            usable_rows=0,
        )

    sampled = usable = 0
    for row in _sample_csv_rows(path, limit=sample_limit):
        sampled += 1
        if (
            (row.get("ip_right_type") or "").strip().lower() == "trade_mark"
            and (row.get("application_number") or "").strip()
        ):
            usable += 1
    warnings = () if usable or not sampled else ("sample contains no trade_mark rows",)
    return _result(
        source_kind=f"AU_IPGOD_{table}",
        path=path,
        sampled_rows=sampled,
        usable_rows=usable,
        warnings=warnings,
    )


def inspect_ca_st96(path: Path, *, sample_limit: int = 100) -> SourcePreflight:
    sampled = usable = 0
    operations: set[str] = set()
    for record in iter_cipo_records(path):
        sampled += 1
        if record.get("application_number"):
            usable += 1
        operation = str(record.get("operation_category") or "").upper()
        if operation:
            operations.add(operation)
        if sampled >= sample_limit:
            break

    warnings: list[str] = []
    unexpected = sorted(operation for operation in operations if operation not in {"UPDATE", "DELETE"})
    if unexpected:
        warnings.append(f"unexpected operation categories: {unexpected}")
    if not usable:
        warnings.append("no usable ST.96 trademark records found in sample")
    return _result(
        source_kind="CA_ST96",
        path=path,
        sampled_rows=sampled,
        usable_rows=usable,
        warnings=tuple(warnings),
    )
