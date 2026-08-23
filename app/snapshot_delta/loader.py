"""Snapshot-first CSV loading primitives.

The loader intentionally produces normalized snapshot rows only.
Persistence and projection are handled by later pipeline stages.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any, Iterator


def _allow_large_csv_fields() -> None:
    """Raise CPython's small CSV-field default for authoritative JSON columns."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


class SnapshotCsvLoader:
    """Minimal deterministic CSV snapshot reader."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @staticmethod
    def _dict_reader(source) -> csv.DictReader:
        _allow_large_csv_fields()
        return csv.DictReader(source)

    def fieldnames(self) -> tuple[str, ...]:
        with self.path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = self._dict_reader(source)
            return tuple(reader.fieldnames or ())

    def rows(self) -> Iterator[dict[str, Any]]:
        with self.path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = self._dict_reader(source)
            for row in reader:
                yield dict(row)

    def count(self) -> int:
        """Count data rows without allocating a dictionary for every record."""
        _allow_large_csv_fields()
        with self.path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            try:
                next(reader)
            except StopIteration:
                return 0
            return sum(1 for _ in reader)
