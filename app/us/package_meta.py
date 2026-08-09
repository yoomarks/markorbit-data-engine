from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re


HISTORY_RANK_MAJOR = 1_000_000_000_000_000
DAILY_RANK_MAJOR = 3_000_000_000_000_000
UNKNOWN_RANK_MAJOR = 500_000_000_000_000


@dataclass(frozen=True)
class USPackageDescriptor:
    package_kind: str
    partition_dimension: str
    partition_value: str
    source_period_start: date | None
    source_period_end: date | None
    source_sequence: int

    def source_rank(self, package_sequence: int) -> int:
        revision = int(package_sequence) % 1_000_000
        if self.package_kind == "HISTORICAL_APPLICATIONS":
            return HISTORY_RANK_MAJOR + self.source_sequence * 1_000_000 + revision
        if self.package_kind == "DAILY_APPLICATIONS":
            return DAILY_RANK_MAJOR + self.source_sequence * 1_000_000 + revision
        return UNKNOWN_RANK_MAJOR + revision


UNKNOWN_DESCRIPTOR = USPackageDescriptor(
    package_kind="UNKNOWN",
    partition_dimension="",
    partition_value="",
    source_period_start=None,
    source_period_end=None,
    source_sequence=0,
)


def _expand_two_digit_year(value: int) -> int:
    # USPTO daily application files use a two-digit year. Keep a fixed pivot so
    # package precedence never depends on the machine clock.
    return 1900 + value if value >= 70 else 2000 + value


def _ymd(raw: str) -> date | None:
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def infer_us_package_descriptor(path: Path | str) -> USPackageDescriptor:
    stem = Path(path).stem.strip().lower()

    # USPTO historical application snapshot parts are named like:
    # apc18840407-20251231-05.zip
    history = re.fullmatch(r"apc(\d{8})-(\d{8})-(\d+)", stem)
    if history:
        start = _ymd(history.group(1))
        end = _ymd(history.group(2))
        part = int(history.group(3))
        if start is None or end is None or start > end or part < 0:
            return UNKNOWN_DESCRIPTOR
        return USPackageDescriptor(
            package_kind="HISTORICAL_APPLICATIONS",
            partition_dimension="COVERAGE_RANGE_PART",
            partition_value=f"{start.isoformat()}/{end.isoformat()}#{part:03d}",
            source_period_start=start,
            source_period_end=end,
            source_sequence=(end.year * 10_000 + end.month * 100 + end.day) * 1_000 + part,
        )

    daily = re.fullmatch(r"apc(\d{2})(\d{2})(\d{2})", stem)
    if not daily:
        return UNKNOWN_DESCRIPTOR
    year = _expand_two_digit_year(int(daily.group(1)))
    month = int(daily.group(2))
    day = int(daily.group(3))
    try:
        update_date = date(year, month, day)
    except ValueError:
        return UNKNOWN_DESCRIPTOR
    return USPackageDescriptor(
        package_kind="DAILY_APPLICATIONS",
        partition_dimension="UPDATE_DATE",
        partition_value=update_date.isoformat(),
        source_period_start=update_date,
        source_period_end=update_date,
        source_sequence=year * 10_000 + month * 100 + day,
    )
