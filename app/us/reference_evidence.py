from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def evidence_source_candidates(
    raw_root: Path,
    document_name: str,
    *,
    family: str,
) -> tuple[Path, ...]:
    if family not in {"status", "event", "interpretation"}:
        raise ValueError(f"unsupported evidence family: {family}")
    base = raw_root / "reference" / "us"
    return (
        base / family / document_name,
        base / document_name,
    )


def evidence_source_path(
    raw_root: Path,
    document_name: str,
    *,
    family: str,
) -> Path:
    candidates = evidence_source_candidates(raw_root, document_name, family=family)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def verify_source_evidence(
    metadata: dict[str, Any] | None,
    raw_root: Path,
    *,
    family: str,
) -> dict[str, Any]:
    if metadata is None:
        return {
            "status": "NOT_READY",
            "reason": "active_reference_missing",
            "path": None,
            "expected_sha256": None,
            "actual_sha256": None,
        }

    document_name = str(metadata.get("source_document_name") or "").strip()
    expected_sha = str(metadata.get("source_document_sha256") or "").strip().lower()
    path = evidence_source_path(raw_root, document_name, family=family)
    base = {
        "path": str(path),
        "expected_sha256": expected_sha,
        "actual_sha256": None,
    }
    if not document_name or len(expected_sha) != 64:
        return {**base, "status": "FAIL", "reason": "reference_evidence_metadata_invalid"}
    if not path.is_file():
        return {**base, "status": "NOT_READY", "reason": "reference_source_file_missing"}

    actual_sha = sha256_file(path).lower()
    if actual_sha != expected_sha:
        return {
            **base,
            "actual_sha256": actual_sha,
            "status": "FAIL",
            "reason": "reference_source_sha256_mismatch",
        }
    return {
        **base,
        "actual_sha256": actual_sha,
        "status": "PASS",
        "reason": None,
    }


def verify_payload_source_file(
    normalized_payload: dict[str, Any],
    payload_path: Path,
) -> dict[str, Any]:
    source = normalized_payload["source"]
    document_name = str(source["document_name"])
    expected_sha = str(source["sha256"]).lower()
    document_path = payload_path.parent / document_name
    if not document_path.is_file():
        raise ValueError(
            f"Official source document is missing beside payload: {document_path}"
        )
    actual_sha = sha256_file(document_path).lower()
    if actual_sha != expected_sha:
        raise ValueError(
            "Official source document SHA-256 does not match payload evidence: "
            f"expected={expected_sha} actual={actual_sha}"
        )
    return {
        "source_document_path": str(document_path),
        "source_document_sha256": actual_sha,
    }
