from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from app.cn.reader import iter_member_rows
from app.cn.zipio import iter_package_members
from app.db import clickhouse_client


PROBE_VERSION = "CN_PRELIM_DISCOVERY_SOURCE_RANGE_PROBE_V2"
DEFAULT_PACKAGE_NAME = "2023_5.zip"
DEFAULT_RAW_ROOT = Path("/data/raw")
MAX_ROWS_TO_READ = 250_000
MAX_BYTES_TO_READ = 268_435_456
READ_OVERFLOW_MODE = "throw"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _locate_package(raw_root: Path, package_name: str) -> Path:
    direct = raw_root / package_name
    if direct.is_file():
        return direct

    matches = sorted(
        (path for path in raw_root.rglob(package_name) if path.is_file()),
        key=lambda path: str(path),
    )
    if not matches:
        raise RuntimeError(
            f"Source package {package_name!r} was not found below {raw_root}."
        )
    if len(matches) > 1:
        rendered = ", ".join(str(path) for path in matches[:8])
        raise RuntimeError(
            f"Source package {package_name!r} is ambiguous below {raw_root}: {rendered}"
        )
    return matches[0]


def _source_preliminary_publication_numbers(
    package_path: Path,
    *,
    max_candidates: int,
) -> list[str]:
    if max_candidates < 4:
        raise ValueError("max_candidates must be at least 4")

    seen: set[str] = set()
    candidates: list[str] = []
    basic_members = 0

    for member in iter_package_members(package_path):
        if member.schema is None or member.schema.role != "basic":
            continue
        basic_members += 1
        _profile, rows = iter_member_rows(member, forced_encoding="auto", profile_only=False)
        try:
            for parsed in rows:
                application_number = parsed.record.get("application_number", "").strip()
                prelim_pub_date = parsed.record.get("prelim_pub_date", "").strip()
                if not application_number or not prelim_pub_date:
                    continue
                if application_number in seen:
                    continue
                seen.add(application_number)
                candidates.append(application_number)
                if len(candidates) >= max_candidates:
                    return candidates
        finally:
            close = getattr(rows, "close", None)
            if callable(close):
                close()

    if basic_members == 0:
        raise RuntimeError(
            f"Source package {package_path.name!r} has no classified CN basic member."
        )
    return candidates


def _common_prefix_length(left: str, right: str) -> int:
    count = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        count += 1
    return count


def _window_score(start: str, end: str) -> tuple[int, int, int, str, str]:
    if start.isdigit() and end.isdigit() and len(start) == len(end):
        return (0, int(end) - int(start), -_common_prefix_length(start, end), start, end)
    return (
        1,
        len(end) + len(start),
        -_common_prefix_length(start, end),
        start,
        end,
    )


def _source_windows(numbers: Iterable[str]) -> list[tuple[str, str]]:
    ordered = sorted(set(numbers))
    windows: list[tuple[str, str]] = []
    for index in range(0, max(0, len(ordered) - 3)):
        start = ordered[index]
        end = ordered[index + 3]
        if start < end:
            windows.append((start, end))
    windows.sort(key=lambda item: _window_score(item[0], item[1]))
    return windows


def _bounded_current_candidates(client, start: str, end: str) -> list[tuple[str, str]]:
    sql = f"""
        SELECT application_number, toString(case_id)
        FROM markorbit_facts.cn_case_current FINAL
        WHERE application_number >= {_sql_string(start)}
          AND application_number < {_sql_string(end)}
          AND is_deleted = 0
          AND prelim_pub_date IS NOT NULL
        ORDER BY application_number ASC, toString(case_id) ASC
        LIMIT 3
        SETTINGS
            max_rows_to_read={MAX_ROWS_TO_READ},
            max_bytes_to_read={MAX_BYTES_TO_READ},
            read_overflow_mode='{READ_OVERFLOW_MODE}'
    """
    return [(str(row[0]), str(row[1])) for row in client.query(sql).result_rows]


def _is_budget_failure(exc: Exception) -> bool:
    text = str(exc).upper()
    return any(
        marker in text
        for marker in (
            "TOO_MANY_ROWS",
            "TOO_MANY_BYTES",
            "LIMIT FOR ROWS OR BYTES TO READ EXCEEDED",
        )
    )


def find_acceptance_range(
    *,
    raw_root: Path,
    package_name: str,
    max_source_candidates: int,
    max_validation_windows: int,
) -> dict[str, object]:
    package_path = _locate_package(raw_root, package_name)
    source_candidates = _source_preliminary_publication_numbers(
        package_path,
        max_candidates=max_source_candidates,
    )
    if len(source_candidates) < 4:
        raise RuntimeError(
            f"Source package produced only {len(source_candidates)} unique preliminary-publication "
            "application numbers; at least four are required."
        )

    windows = _source_windows(source_candidates)
    if not windows:
        raise RuntimeError("Source package did not produce a usable lexical application-number range.")

    client = clickhouse_client()
    budget_rejections = 0
    checked = 0
    try:
        for start, end in windows[:max_validation_windows]:
            checked += 1
            try:
                current = _bounded_current_candidates(client, start, end)
            except Exception as exc:
                if _is_budget_failure(exc):
                    budget_rejections += 1
                    continue
                raise

            if len(current) < 3:
                continue

            return {
                "probe_version": PROBE_VERSION,
                "status": "PASS",
                "source": {
                    "package_name": package_path.name,
                    "basic_parser": "app.cn.reader.iter_member_rows",
                    "source_candidate_sample_count": len(source_candidates),
                },
                "application_number_start": start,
                "application_number_end": end,
                "verified_current_candidates": [
                    {"application_number": application_number, "case_id": case_id}
                    for application_number, case_id in current
                ],
                "verified_current_candidate_count": len(current),
                "validation_windows_checked": checked,
                "budget_rejections": budget_rejections,
                "read_budget": {
                    "max_rows_to_read": MAX_ROWS_TO_READ,
                    "max_bytes_to_read": MAX_BYTES_TO_READ,
                    "overflow_mode": READ_OVERFLOW_MODE,
                },
                "range_source": "RAW_SOURCE_PACKAGE",
                "fact_table_range_discovery": False,
                "read_only": True,
            }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    raise RuntimeError(
        "No source-derived three-candidate current range passed within "
        f"{min(len(windows), max_validation_windows)} explicitly bounded validation windows; "
        f"budget_rejections={budget_rejections}. No unbounded fact-table discovery was attempted."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Find a tiny CN preliminary-publication Discovery acceptance range from a raw "
            "source package, then verify only explicit source-derived ranges against current facts."
        )
    )
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--package-name", default=DEFAULT_PACKAGE_NAME)
    parser.add_argument("--max-source-candidates", type=int, default=1024)
    parser.add_argument("--max-validation-windows", type=int, default=64)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.max_source_candidates < 4 or args.max_source_candidates > 10_000:
        raise SystemExit("--max-source-candidates must be between 4 and 10000")
    if args.max_validation_windows < 1 or args.max_validation_windows > 256:
        raise SystemExit("--max-validation-windows must be between 1 and 256")

    receipt = find_acceptance_range(
        raw_root=args.raw_root,
        package_name=str(args.package_name),
        max_source_candidates=int(args.max_source_candidates),
        max_validation_windows=int(args.max_validation_windows),
    )
    print("CN_PRELIM_DISCOVERY_ACCEPTANCE_RANGE_PROBE_PASS")
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
