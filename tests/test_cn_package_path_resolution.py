from __future__ import annotations

import hashlib
from pathlib import Path

from app.jobs import _resolve_package_path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_resolve_package_path_recovers_sha_matching_archive(tmp_path: Path):
    raw = tmp_path / "raw"
    archive = raw / "archive" / "cn"
    archive.mkdir(parents=True)
    archived = archive / "2003.zip"
    archived.write_bytes(b"authoritative-package")

    package = {
        "file_path": str(raw / "incoming" / "cn" / "2003.zip"),
        "file_name": "2003.zip",
        "sha256": _sha256(archived),
    }

    assert _resolve_package_path(package, raw) == archived


def test_resolve_package_path_rejects_wrong_archive_hash(tmp_path: Path):
    raw = tmp_path / "raw"
    archive = raw / "archive" / "cn"
    archive.mkdir(parents=True)
    archived = archive / "2003.zip"
    archived.write_bytes(b"wrong-package")

    package = {
        "file_path": str(raw / "incoming" / "cn" / "2003.zip"),
        "file_name": "2003.zip",
        "sha256": hashlib.sha256(b"expected-package").hexdigest(),
    }

    assert _resolve_package_path(package, raw) is None


def test_resolve_package_path_accepts_declared_existing_path(tmp_path: Path):
    raw = tmp_path / "raw"
    incoming = raw / "incoming" / "cn"
    incoming.mkdir(parents=True)
    declared = incoming / "2003.zip"
    declared.write_bytes(b"package")

    package = {
        "file_path": str(declared),
        "file_name": "2003.zip",
        "sha256": _sha256(declared),
    }

    assert _resolve_package_path(package, raw) == declared
