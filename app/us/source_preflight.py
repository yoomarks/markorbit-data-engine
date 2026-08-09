from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import io
import json
from pathlib import Path
import re
from typing import Any, BinaryIO
import xml.etree.ElementTree as ET
import zipfile

from app.config import get_settings
from app.scanner import sha256_file
from app.us.audit_real_data_v2 import historical_part_completeness
from app.us.package_meta import USPackageDescriptor, infer_us_package_descriptor


PREFLIGHT_VERSION = "US_SOURCE_PREFLIGHT_V1"
SOURCE_SUFFIXES = {".zip", ".xml"}
UNSUPPORTED_SOURCE_SUFFIXES = {".gz"}
ARCHIVE_DIGEST_SUFFIX_RE = re.compile(r"^(?P<base>.+)_[0-9a-fA-F]{8}$")


def _descriptor_for_path(path: Path) -> tuple[USPackageDescriptor, bool]:
    descriptor = infer_us_package_descriptor(path)
    if descriptor.package_kind != "UNKNOWN":
        return descriptor, False
    match = ARCHIVE_DIGEST_SUFFIX_RE.match(path.stem)
    if not match:
        return descriptor, False
    recovered = path.with_name(match.group("base") + path.suffix)
    descriptor = infer_us_package_descriptor(recovered)
    return descriptor, descriptor.package_kind != "UNKNOWN"


def _shallow_xml_check(stream: BinaryIO) -> dict[str, Any]:
    try:
        iterator = ET.iterparse(stream, events=("start",))
        _event, root = next(iterator)
        return {"readable": True, "root_tag": str(root.tag), "deep_validated": False}
    except StopIteration:
        return {"readable": False, "error": "empty_xml", "deep_validated": False}
    except (ET.ParseError, OSError, ValueError) as exc:
        return {
            "readable": False,
            "error": f"{type(exc).__name__}: {exc}",
            "deep_validated": False,
        }


def _deep_xml_check(stream: BinaryIO) -> dict[str, Any]:
    root_tag = ""
    count = 0
    try:
        for _event, element in ET.iterparse(stream, events=("start", "end")):
            if not root_tag:
                root_tag = str(element.tag)
            if _event == "end":
                count += 1
                element.clear()
        if not root_tag:
            return {"readable": False, "error": "empty_xml", "deep_validated": True}
        return {
            "readable": True,
            "root_tag": root_tag,
            "deep_validated": True,
            "elements_parsed": count,
        }
    except (ET.ParseError, OSError, ValueError) as exc:
        return {
            "readable": False,
            "error": f"{type(exc).__name__}: {exc}",
            "deep_validated": True,
        }


def _inspect_xml(path: Path, *, deep_source_test: bool) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            return (
                _deep_xml_check(stream)
                if deep_source_test
                else _shallow_xml_check(stream)
            )
    except OSError as exc:
        return {
            "readable": False,
            "error": f"{type(exc).__name__}: {exc}",
            "deep_validated": deep_source_test,
        }


def _inspect_zip(path: Path, *, deep_source_test: bool) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            xml_members = [item for item in members if item.filename.lower().endswith(".xml")]
            duplicate_names = sorted(
                name for name, count in Counter(item.filename for item in members).items() if count > 1
            )
            encrypted = sorted(item.filename for item in members if item.flag_bits & 0x1)
            result: dict[str, Any] = {
                "readable": bool(xml_members) and not duplicate_names and not encrypted,
                "member_count": len(members),
                "xml_member_count": len(xml_members),
                "xml_members": [item.filename for item in xml_members[:50]],
                "duplicate_member_names": duplicate_names,
                "encrypted_members": encrypted,
                "uncompressed_bytes": sum(item.file_size for item in members),
                "deep_validated": deep_source_test,
            }
            if not xml_members:
                result["error"] = "zip_contains_no_xml_members"
                return result
            if duplicate_names:
                result["error"] = "zip_contains_duplicate_member_names"
                return result
            if encrypted:
                result["error"] = "zip_contains_encrypted_members"
                return result

            # Confirm at least the first XML member can be opened in shallow mode.
            with archive.open(xml_members[0], "r") as stream:
                xml_check = (
                    _deep_xml_check(stream)
                    if deep_source_test
                    else _shallow_xml_check(stream)
                )
            result["first_xml_check"] = xml_check
            if not xml_check.get("readable"):
                result["readable"] = False
                result["error"] = "first_xml_member_unreadable"
                return result

            if deep_source_test:
                bad_crc = archive.testzip()
                if bad_crc:
                    result["readable"] = False
                    result["error"] = f"zip_crc_failure:{bad_crc}"
                    return result
                deep_failures: list[dict[str, str]] = []
                # First member was already fully parsed above. Validate the rest.
                for member in xml_members[1:]:
                    with archive.open(member, "r") as stream:
                        check = _deep_xml_check(stream)
                    if not check.get("readable"):
                        deep_failures.append(
                            {"member": member.filename, "error": str(check.get("error") or "")}
                        )
                result["deep_xml_failures"] = deep_failures
                if deep_failures:
                    result["readable"] = False
                    result["error"] = "zip_contains_malformed_xml"
            return result
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        return {
            "readable": False,
            "error": f"{type(exc).__name__}: {exc}",
            "deep_validated": deep_source_test,
        }


def _inspect_source(path: Path, *, deep_source_test: bool) -> dict[str, Any]:
    if path.suffix.lower() == ".xml":
        return _inspect_xml(path, deep_source_test=deep_source_test)
    if path.suffix.lower() == ".zip":
        return _inspect_zip(path, deep_source_test=deep_source_test)
    return {"readable": False, "error": "unsupported_source_suffix"}


def _scan_directory(
    directory: Path,
    *,
    location: str,
    deep_source_test: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    if not directory.exists():
        return sources, issues

    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in UNSUPPORTED_SOURCE_SUFFIXES:
            issues.append(
                {
                    "type": "UNSUPPORTED_US_SOURCE_SUFFIX",
                    "file_name": path.name,
                    "path": str(path),
                    "location": location,
                }
            )
            continue
        if suffix not in SOURCE_SUFFIXES:
            continue

        descriptor, recovered_from_archive_suffix = _descriptor_for_path(path)
        if descriptor.package_kind == "UNKNOWN":
            issues.append(
                {
                    "type": "UNKNOWN_US_PACKAGE_PRECEDENCE",
                    "file_name": path.name,
                    "path": str(path),
                    "location": location,
                }
            )
            continue

        try:
            digest = sha256_file(path).lower()
            file_size = path.stat().st_size
        except OSError as exc:
            issues.append(
                {
                    "type": "SOURCE_FILE_UNREADABLE",
                    "file_name": path.name,
                    "path": str(path),
                    "location": location,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        inspection = _inspect_source(path, deep_source_test=deep_source_test)
        source = {
            "file_name": path.name,
            "path": str(path),
            "location": location,
            "file_size": file_size,
            "sha256": digest,
            "package_kind": descriptor.package_kind,
            "partition_dimension": descriptor.partition_dimension,
            "partition_value": descriptor.partition_value,
            "source_period_start": (
                descriptor.source_period_start.isoformat()
                if descriptor.source_period_start
                else None
            ),
            "source_period_end": (
                descriptor.source_period_end.isoformat()
                if descriptor.source_period_end
                else None
            ),
            "source_sequence": descriptor.source_sequence,
            "archive_digest_suffix_recovered": recovered_from_archive_suffix,
            "inspection": inspection,
        }
        sources.append(source)
        if not inspection.get("readable"):
            issues.append(
                {
                    "type": "SOURCE_CONTAINER_OR_XML_UNREADABLE",
                    "file_name": path.name,
                    "path": str(path),
                    "location": location,
                    "inspection": inspection,
                }
            )
    return sources, issues


def _semantic_groups(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for source in sources:
        grouped[(source["package_kind"], source["partition_value"])].append(source)

    groups: list[dict[str, Any]] = []
    for (kind, partition), rows in sorted(grouped.items()):
        shas = sorted({row["sha256"] for row in rows})
        selected = sorted(
            rows,
            key=lambda row: (0 if row["location"] == "incoming" else 1, row["path"]),
        )[0]
        groups.append(
            {
                "package_kind": kind,
                "partition_value": partition,
                "copy_count": len(rows),
                "distinct_sha256_count": len(shas),
                "sha256": shas,
                "selected_path": selected["path"],
                "selected_location": selected["location"],
                "copies": [
                    {
                        "file_name": row["file_name"],
                        "path": row["path"],
                        "location": row["location"],
                        "sha256": row["sha256"],
                    }
                    for row in sorted(rows, key=lambda row: (row["location"], row["path"]))
                ],
            }
        )
    return groups


def _authoritative_sources(
    sources: list[dict[str, Any]],
    semantic_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_path = {source["path"]: source for source in sources}
    return [
        by_path[group["selected_path"]]
        for group in semantic_groups
        if group["distinct_sha256_count"] == 1
    ]


def _replay_sort_key(source: dict[str, Any]) -> tuple[Any, ...]:
    kind = source["package_kind"]
    if kind == "HISTORICAL_APPLICATIONS":
        return (
            0,
            source.get("source_period_end") or "",
            source.get("source_period_start") or "",
            int(source.get("source_sequence") or 0),
            source["partition_value"],
        )
    if kind == "DAILY_APPLICATIONS":
        return (1, source.get("source_period_end") or "", source["partition_value"])
    return (2, source["partition_value"])


def _calendar_gaps(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dates = sorted(
        {
            date.fromisoformat(str(row["source_period_end"]))
            for row in daily
            if row.get("source_period_end")
        }
    )
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(dates, dates[1:]):
        gap_days = (current - previous).days
        if gap_days > 1:
            gaps.append(
                {
                    "previous": previous.isoformat(),
                    "next": current.isoformat(),
                    "calendar_gap_days": gap_days - 1,
                    "note": (
                        "Informational only. Weekends, federal holidays, or USPTO publication "
                        "schedules can create calendar gaps; use an authoritative manifest before "
                        "calling a daily file missing."
                    ),
                }
            )
    return gaps


def build_preflight(
    raw_root: Path,
    *,
    expected_history_parts: int | None = None,
    deep_source_test: bool = False,
) -> dict[str, Any]:
    incoming = raw_root / "incoming" / "us"
    archive = raw_root / "archive" / "us"
    incoming_sources, incoming_issues = _scan_directory(
        incoming, location="incoming", deep_source_test=deep_source_test
    )
    archive_sources, archive_issues = _scan_directory(
        archive, location="archive", deep_source_test=deep_source_test
    )
    sources = incoming_sources + archive_sources
    issues = incoming_issues + archive_issues

    semantic_groups = _semantic_groups(sources)
    conflicts = [group for group in semantic_groups if group["distinct_sha256_count"] > 1]
    if conflicts:
        issues.extend(
            {
                "type": "SEMANTIC_PARTITION_SHA_CONFLICT",
                "package_kind": group["package_kind"],
                "partition_value": group["partition_value"],
                "copies": group["copies"],
            }
            for group in conflicts
        )

    authoritative = _authoritative_sources(sources, semantic_groups)
    history = [row for row in authoritative if row["package_kind"] == "HISTORICAL_APPLICATIONS"]
    daily = [row for row in authoritative if row["package_kind"] == "DAILY_APPLICATIONS"]
    completeness = historical_part_completeness(
        history,
        expected_history_parts=expected_history_parts,
    )

    not_ready: list[str] = []
    if not history:
        not_ready.append("historical_baseline_missing")
    elif completeness["invalid_partition_count"]:
        not_ready.append("unrecognized_historical_partition_identity")
    if history and completeness["leading_or_interior_gap"]:
        not_ready.append("historical_part_sequence_incomplete")
    if history and expected_history_parts is None:
        not_ready.append("historical_tail_part_count_not_pinned")
    if completeness["missing_expected_parts"]:
        not_ready.append("expected_historical_parts_missing")
    if completeness["unexpected_parts"]:
        not_ready.append("historical_parts_exceed_expected_count")

    baseline_end: date | None = None
    baseline = completeness.get("baseline_coverage")
    if baseline and baseline.get("coverage_end"):
        baseline_end = date.fromisoformat(str(baseline["coverage_end"]))

    unsafe_daily: list[dict[str, Any]] = []
    if baseline_end is not None:
        for row in daily:
            update_date = date.fromisoformat(str(row["source_period_end"]))
            if update_date <= baseline_end:
                unsafe_daily.append(
                    {
                        "file_name": row["file_name"],
                        "path": row["path"],
                        "update_date": update_date.isoformat(),
                        "historical_baseline_end": baseline_end.isoformat(),
                    }
                )
    if unsafe_daily:
        issues.append(
            {
                "type": "DAILY_PACKAGE_NOT_AFTER_HISTORICAL_BASELINE",
                "sources": unsafe_daily,
                "note": (
                    "US source precedence gives every daily package a higher rank than history. "
                    "A daily package on/before the chosen historical snapshot end would therefore "
                    "be unsafe to replay after that snapshot."
                ),
            }
        )

    warnings: list[str] = []
    if not daily:
        warnings.append("no_daily_packages_observed")
    if any(group["copy_count"] > 1 for group in semantic_groups):
        warnings.append("identical_semantic_source_copies_deduplicated")

    hard_issue_types = sorted({str(issue["type"]) for issue in issues})
    status = "FAIL" if hard_issue_types else ("NOT_READY" if not_ready else ("PASS_WITH_WARNINGS" if warnings else "PASS"))

    replay_sources = sorted(authoritative, key=_replay_sort_key)
    plan = [
        {
            "sequence": index,
            "package_kind": source["package_kind"],
            "partition_value": source["partition_value"],
            "file_name": source["file_name"],
            "path": source["path"],
            "location": source["location"],
            "sha256": source["sha256"],
            "needs_staging_from_archive": source["location"] == "archive",
        }
        for index, source in enumerate(replay_sources, start=1)
    ]

    return {
        "status": status,
        "preflight_version": PREFLIGHT_VERSION,
        "safe_to_replay": status in {"PASS", "PASS_WITH_WARNINGS"},
        "raw_root": str(raw_root),
        "deep_source_test": deep_source_test,
        "source_inventory": {
            "incoming_directory": str(incoming),
            "archive_directory": str(archive),
            "physical_source_count": len(sources),
            "semantic_source_count": len(semantic_groups),
            "history_source_count": len(history),
            "daily_source_count": len(daily),
            "sources": sorted(sources, key=_replay_sort_key),
            "semantic_groups": semantic_groups,
        },
        "historical_part_completeness": completeness,
        "historical_baseline_end": baseline_end.isoformat() if baseline_end else None,
        "daily_safety": {
            "unsafe_on_or_before_baseline": unsafe_daily,
            "informational_calendar_gaps": _calendar_gaps(daily),
        },
        "replay_plan": plan,
        "archive_staging_required_count": sum(
            1 for row in plan if row["needs_staging_from_archive"]
        ),
        "hard_issue_types": hard_issue_types,
        "issues": issues,
        "not_ready_reasons": list(dict.fromkeys(not_ready)),
        "warning_reasons": list(dict.fromkeys(warnings)),
        "policy_note": (
            "This is a read-only source-corpus preflight. Calendar gaps between daily packages are "
            "informational only because weekends/holidays/publication schedules are not inferred. "
            "The planner never guesses the historical tail part count and never stages, registers, "
            "ingests, retries, or changes database state."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only USPTO source preflight and deterministic replay planner"
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=None,
        help="Override RAW_DATA_ROOT for local testing.",
    )
    parser.add_argument(
        "--expected-history-parts",
        type=int,
        default=None,
        help="Exact number of parts for the latest historical coverage range.",
    )
    parser.add_argument(
        "--deep-source-test",
        action="store_true",
        help="Run ZIP CRC checks and stream-parse every XML member to EOF.",
    )
    args = parser.parse_args()
    if args.expected_history_parts is not None and args.expected_history_parts < 1:
        parser.error("--expected-history-parts must be at least 1")
    raw_root = args.raw_root or get_settings().raw_data_root
    print(
        json.dumps(
            build_preflight(
                raw_root,
                expected_history_parts=args.expected_history_parts,
                deep_source_test=args.deep_source_test,
            ),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
