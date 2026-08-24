"""Resource preflight for a real Singapore IPOS full-corpus lifecycle."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


IPOS_SG_STORAGE_PREFLIGHT_VERSION = "IPOS_SG_STORAGE_PREFLIGHT_V1"
IPOS_SG_MIN_FREE_BYTES = 8 * 1024**3


@dataclass(frozen=True)
class IposStoragePreflight:
    version: str
    status: str
    free_bytes: int
    total_bytes: int
    largest_retained_snapshot_bytes: int
    required_free_bytes: int
    safe_to_run: bool


def build_ipos_storage_preflight(
    state_directory: str | Path,
    *,
    minimum_free_bytes: int = IPOS_SG_MIN_FREE_BYTES,
    disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
) -> IposStoragePreflight:
    """Require enough free space for a new full snapshot plus lifecycle evidence.

    A changed cycle must keep the previously accepted full snapshot while the new
    multi-GB corpus is downloaded and compared. The requirement therefore scales to
    twice the largest retained full snapshot, with an 8 GiB bootstrap floor.
    """
    if minimum_free_bytes < 1024**3:
        raise ValueError("Singapore minimum free-space floor must be at least 1 GiB")

    state = Path(state_directory)
    state.mkdir(parents=True, exist_ok=True)
    snapshots = state / "snapshots"
    retained_sizes = (
        [path.stat().st_size for path in snapshots.glob("*.csv")]
        if snapshots.exists()
        else []
    )
    largest = max(retained_sizes, default=0)
    required = max(int(minimum_free_bytes), largest * 2)
    usage = disk_usage(state)
    free = int(usage.free)
    total = int(usage.total)
    safe = free >= required
    return IposStoragePreflight(
        version=IPOS_SG_STORAGE_PREFLIGHT_VERSION,
        status="PASS" if safe else "BLOCKED",
        free_bytes=free,
        total_bytes=total,
        largest_retained_snapshot_bytes=largest,
        required_free_bytes=required,
        safe_to_run=safe,
    )


def storage_preflight_payload(preflight: IposStoragePreflight) -> dict[str, Any]:
    return asdict(preflight)
