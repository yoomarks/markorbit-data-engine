"""Immutable UTF-16 ZIP stock staging for historical UKIPO Domestic and Madrid-IR.

Never touches production database or marks historical statuses as current.
All source rows (including co-owners and malformed rows) retain their
source-archive SHA, UTF-16 member identity and physical CSV record ordinal.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StockSpec:
    stream: str
    zip_name: str
    member: str
    source_sha256: str
    base_columns: tuple[str, ...]
    expected_rows: int
    expected_quarantine: int
    expected_unique_marks: int
    expected_duplicate_marks: int
    mark_prefix: str


COMMON = ("Trade Mark", "Hyperlink", "Mark Text", "Name")
DOMESTIC = COMMON + (
    "Postcode",
    "Region",
    "Country",
    "Status",
    "Category of Mark",
    "Mark Type",
    "Series",
    "No of Marks in Series",
    "Filed",
    "Published",
    "Registered",
    "Expired",
    "Renewal Due Date",
)
MADRID = COMMON + (
    "Country",
    "Status",
    "Category of Mark",
    "Mark Type",
    "Date of Designation",
    "Published",
    "Protected",
    "Expired",
)
CLASS_COLUMNS = tuple(f"Class{i}" for i in range(1, 46))
SPECS = {
    "DOMESTIC": StockSpec(
        "DOMESTIC",
        "opendatadomestic.zip",
        "OpenDataDomestic.txt",
        "3b6063bed36a78e8a04f10a5383f2881f4072a1be13e706a81ab097fb56ee571",
        DOMESTIC,
        1_188_992,
        106,
        1_171_979,
        16_907,
        "UK",
    ),
    "MADRID_IR": StockSpec(
        "MADRID_IR",
        "opendataIR.zip",
        "OpenDataIR.txt",
        "2a93bb45f0c40c69813628b6f2ab3a6a443f6d0504d8edf009c51027f28f8c85",
        MADRID,
        109_695,
        8,
        108_279,
        1_408,
        "WO",
    ),
}


class GBStockError(RuntimeError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise GBStockError(reason)


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def encoded(record: dict) -> bytes:
    return (
        json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def stage_snapshot(
    source: Path,
    spec: StockSpec,
    output: Path,
    *,
    expected_sha: str,
    write: bool = False,
) -> dict:
    require(source.is_file() and source.name == spec.zip_name, "unexpected UKIPO ZIP")
    require(
        re.fullmatch(r"[0-9a-f]{64}", expected_sha) is not None
        and expected_sha == spec.source_sha256
        and sha_file(source) == expected_sha,
        "UKIPO historical source ZIP SHA drift",
    )
    stem = spec.stream.lower() + "-" + expected_sha[:12]
    rows_path = output / f"{stem}-rows.jsonl"
    bad_path = output / f"{stem}-quarantine.jsonl"
    manifest_path = output / f"{stem}-manifest.json"
    if write:
        require(
            not any(p.exists() for p in (rows_path, bad_path, manifest_path)),
            "immutable GB stock staging output already exists",
        )
        output.mkdir(parents=True, exist_ok=True)
    row_hash = hashlib.sha256()
    bad_hash = hashlib.sha256()
    counts = Counter()
    seen: set[str] = set()
    rows_stream = bad_stream = None
    try:
        if write:
            rows_stream = rows_path.open("xb")
            bad_stream = bad_path.open("xb")
        with zipfile.ZipFile(source) as archive:
            members = [i for i in archive.infolist() if not i.is_dir()]
            require(
                len(members) == 1 and members[0].filename == spec.member,
                "unexpected source ZIP member or extra evidence file",
            )
            info = members[0]
            require(
                0 < info.file_size < 2 * 1024**3, "UKIPO UTF-16 member outside reviewed size bounds"
            )
            with archive.open(info) as binary:
                require(binary.read(2) == b"\xff\xfe", "UKIPO source is not UTF-16 LE with BOM")
            with (
                archive.open(info) as binary,
                io.TextIOWrapper(binary, encoding="utf-16", newline="") as text,
            ):
                reader = csv.reader(text, delimiter="|")
                columns = next(reader)
                require(
                    columns == list(spec.base_columns + CLASS_COLUMNS),
                    "UKIPO stream/header/45 class columns drift",
                )
                field_count = len(columns)
                # The source contains Domestic AA/AB series suffixes, while
                # the Madrid-IR snapshot only uses single-letter suffixes.
                key_pattern = re.compile(
                    spec.mark_prefix
                    + (r"\d+[A-Z]{0,2}" if spec.stream == "DOMESTIC" else r"\d+[A-Z]?")
                )
                for ordinal, cells in enumerate(reader, 1):
                    counts["records"] += 1
                    reason = None
                    if len(cells) != field_count:
                        reason = "FIELD_COUNT"
                    else:
                        mark = cells[0].strip()
                        if key_pattern.fullmatch(mark) is None:
                            reason = "MARK_ID"
                        elif any(
                            value.strip() not in ("", "0", "1")
                            for value in cells[len(spec.base_columns) :]
                        ):
                            reason = "CLASS_FLAG"
                    if reason:
                        counts["quarantine"] += 1
                        payload = {
                            "kind": "UKIPO_GB_STOCK_QUARANTINE_V2",
                            "source_stream": spec.stream,
                            "source_archive_sha256": expected_sha,
                            "source_member": spec.member,
                            "source_row_ordinal": ordinal,
                            "reason": reason,
                            "cells": cells,
                        }
                        line = encoded(payload)
                        bad_hash.update(line)
                        if bad_stream is not None:
                            bad_stream.write(line)
                        continue
                    mark = cells[0].strip()
                    if mark in seen:
                        counts["duplicate_mark_rows"] += 1
                    else:
                        seen.add(mark)
                    raw_fields = dict(zip(columns, cells, strict=True))
                    classes = [i for i in range(1, 46) if raw_fields[f"Class{i}"].strip() == "1"]
                    payload = {
                        "kind": "UKIPO_GB_STOCK_SOURCE_ROW_V2",
                        "source_stream": spec.stream,
                        "application_number": mark,
                        "applicant_name_raw": raw_fields["Name"].strip(),
                        "source_status_raw": raw_fields["Status"].strip(),
                        "nice_classes": classes,
                        "source_archive_sha256": expected_sha,
                        "source_member": spec.member,
                        "source_row_ordinal": ordinal,
                        "source_cells_sha256": hashlib.sha256(
                            json.dumps(cells, ensure_ascii=False, separators=(",", ":")).encode(
                                "utf-8"
                            )
                        ).hexdigest(),
                        "source_fields": raw_fields,
                        "historical_source_only": True,
                        "current_state_verified": False,
                    }
                    line = encoded(payload)
                    row_hash.update(line)
                    if rows_stream is not None:
                        rows_stream.write(line)
                    counts["accepted"] += 1
                require(
                    counts["records"] == spec.expected_rows
                    and counts["quarantine"] == spec.expected_quarantine
                    and len(seen) == spec.expected_unique_marks
                    and counts["duplicate_mark_rows"] == spec.expected_duplicate_marks,
                    "frozen UKIPO stock cardinality/co-owner expectations drift: "
                    + repr(dict(counts))
                    + " distinct_marks="
                    + str(len(seen)),
                )
        manifest = {
            "kind": "UKIPO_GB_HISTORICAL_STOCK_STAGING_V2",
            "source_stream": spec.stream,
            "source_zip_filename": spec.zip_name,
            "source_archive_sha256": expected_sha,
            "source_member": spec.member,
            "source_member_bytes": info.file_size,
            "source_member_crc32": format(info.CRC, "08x"),
            "row_count": counts["records"],
            "accepted_source_rows": counts["accepted"],
            "quarantined_source_rows": counts["quarantine"],
            "distinct_application_numbers": len(seen),
            "duplicate_mark_rows_preserved": counts["duplicate_mark_rows"],
            "accepted_jsonl_sha256": row_hash.hexdigest(),
            "quarantine_jsonl_sha256": bad_hash.hexdigest(),
            "historical_source_only": True,
            "current_state_verified": False,
            "data_engine_ingested": False,
            "schema_migration_applied": False,
            "output_written": write,
        }
        if write:
            rows_stream.flush()
            bad_stream.flush()
            rows_stream.close()
            bad_stream.close()
            rows_stream = bad_stream = None
            manifest["rows_path"] = str(rows_path)
            manifest["quarantine_path"] = str(bad_path)
            with manifest_path.open("x", encoding="utf-8") as file:
                json.dump(manifest, file, ensure_ascii=False, sort_keys=True, indent=2)
                file.write("\n")
            manifest["manifest_path"] = str(manifest_path)
            manifest["manifest_sha256"] = sha_file(manifest_path)
        return manifest
    finally:
        if rows_stream is not None:
            rows_stream.close()
        if bad_stream is not None:
            bad_stream.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", choices=tuple(SPECS), required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--stage", action="store_true")
    args = parser.parse_args()
    spec = SPECS[args.stream]
    result = stage_snapshot(
        args.source_root / spec.zip_name,
        spec,
        args.output,
        expected_sha=args.source_sha,
        write=args.stage,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
