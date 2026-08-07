from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re


BASE_RANK_MAJOR = 1_000_000_000_000_000
MONTHLY_RANK_MAJOR = 2_000_000_000_000_000
UNKNOWN_RANK_MAJOR = 500_000_000_000_000


@dataclass(frozen=True)
class PackageDescriptor:
    package_kind: str
    partition_dimension: str
    partition_value: str
    source_period_start: date | None
    source_period_end: date | None
    source_sequence: int

    def source_rank(self, package_sequence: int) -> int:
        revision = int(package_sequence) % 1_000_000
        if self.package_kind == "MONTHLY_PATCH":
            return MONTHLY_RANK_MAJOR + self.source_sequence * 1_000_000 + revision
        if self.package_kind == "BASE_PARTITION":
            # Base files are filing-year partitions, not chronological snapshots.
            # Their year must not outrank later monthly patches. Package sequence is
            # only a deterministic tie-breaker for unexpected overlaps.
            return BASE_RANK_MAJOR + revision
        return UNKNOWN_RANK_MAJOR + revision


UNKNOWN_DESCRIPTOR = PackageDescriptor(
    package_kind="UNKNOWN",
    partition_dimension="",
    partition_value="",
    source_period_start=None,
    source_period_end=None,
    source_sequence=0,
)


def infer_package_descriptor(path: Path | str) -> PackageDescriptor:
    stem = Path(path).stem.strip()

    base_match = re.fullmatch(r"(19|20)\d{2}", stem)
    if base_match:
        year = int(stem)
        return PackageDescriptor(
            package_kind="BASE_PARTITION",
            partition_dimension="FILING_YEAR",
            partition_value=str(year),
            source_period_start=None,
            source_period_end=None,
            source_sequence=year,
        )

    monthly_match = re.fullmatch(r"((?:19|20)\d{2})[_-](0?[1-9]|1[0-2])", stem)
    if monthly_match:
        year = int(monthly_match.group(1))
        month = int(monthly_match.group(2))
        last_day = monthrange(year, month)[1]
        return PackageDescriptor(
            package_kind="MONTHLY_PATCH",
            partition_dimension="UPDATE_MONTH",
            partition_value=f"{year:04d}-{month:02d}",
            source_period_start=date(year, month, 1),
            source_period_end=date(year, month, last_day),
            source_sequence=year * 100 + month,
        )

    return UNKNOWN_DESCRIPTOR
