from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re


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
    # USPTO TDXF daily application package names use a two-digit year. Daily
    # application files are a modern feed; retain a deterministic pivot instead
    # of depending on the machine's current year.
    return 1900 + value if value >= 70 else 2000 + value


def infer_us_package_descriptor(path: Path | str) -> USPackageDescriptor:
    stem = Path(path).stem.strip().lower()
    match = re.fullmatch(r"apc(\d{2})(\d{2})(\d{2})", stem)
    if not match:
        return UNKNOWN_DESCRIPTOR

    year = _expand_two_digit_year(int(match.group(1)))
    month = int(match.group(2))
    day = int(match.group(3))
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
