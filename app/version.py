from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache
def engine_version() -> str:
    """Return the repository/runtime engine release marker.

    The same relative path works in a source checkout and in the API/worker image:
    ``app/version.py`` lives one directory below the repository/image root where
    ``VERSION`` is copied. Keeping health metadata tied to the release marker
    prevents product-version strings from drifting across code and documentation.
    """

    version_file = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Engine VERSION file is unavailable: {version_file}") from exc
    if not version:
        raise RuntimeError(f"Engine VERSION file is empty: {version_file}")
    return version
