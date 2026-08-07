from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.domain import DiscoveredPackage


CHUNK_SIZE = 8 * 1024 * 1024
SUPPORTED_SUFFIXES = {".zip", ".xml", ".gz"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def discover_packages(directory: Path, jurisdiction: str) -> Iterable[DiscoveredPackage]:
    if not directory.exists():
        return []

    packages: list[DiscoveredPackage] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        stat = path.stat()
        packages.append(
            DiscoveredPackage(
                jurisdiction=jurisdiction,
                path=path,
                file_name=path.name,
                file_size=stat.st_size,
                sha256=sha256_file(path),
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )
        )
    return packages
