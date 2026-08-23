"""Snapshot-first CSV loading primitives.

The loader intentionally produces normalized snapshot rows only.
Persistence and projection are handled by later pipeline stages.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterator


class SnapshotCsvLoader:
    """Minimal deterministic CSV snapshot reader."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fieldnames(self) -> tuple[str, ...]:
        with self.path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            return tuple(reader.fieldnames or ())

    def rows(self) -> Iterator[dict[str, Any]]:
        with self.path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            for row in reader:
                yield dict(row)

    def count(self) -> int:
        return sum(1 for _ in self.rows())
