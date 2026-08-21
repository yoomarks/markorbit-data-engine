from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.db import postgres_conn
from app.global_trademarks.schema import ensure_country_trademark_schemas


_SOURCE_NAMESPACE = uuid.UUID("c37b1f26-5d5a-4afb-9708-a601903b1ad1")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SourceObjectPin:
    object_id: uuid.UUID
    jurisdiction: str
    source_id: str
    object_key: str
    sha256: str
    path: Path


_SOURCE_OBJECT_PIN: ContextVar[SourceObjectPin | None] = ContextVar(
    "global_trademark_source_object_pin",
    default=None,
)


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("expected a 64-character SHA256 hex digest")
    return normalized


def source_object_id(*, source_id: str, object_key: str, sha256: str) -> uuid.UUID:
    material = f"{source_id}\0{object_key}\0{sha256}"
    return uuid.uuid5(_SOURCE_NAMESPACE, material)


def _merge_source_metadata(object_id: uuid.UUID, metadata: dict[str, Any] | None) -> None:
    if not metadata:
        return
    payload = json.dumps(metadata, ensure_ascii=False)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE acquisition.global_trademark_source_object
                SET metadata = metadata || %s::jsonb
                WHERE object_id = %s
                """,
                (payload, object_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"missing pinned global trademark source object: {object_id}")
        conn.commit()


def arm_registered_source_object(
    *,
    object_id: uuid.UUID,
    jurisdiction: str,
    source_id: str,
    object_key: str,
    path: Path,
    expected_sha256: str,
) -> None:
    """Arm a one-shot source identity guard for the next loader registration.

    ``register_plan_source`` records the preflight-approved object first. The loader's
    normal ``register_source_object`` call then consumes this pin, re-hashes the file
    immediately before ingestion, and refuses to create or switch to a different source
    object if the path was replaced after planning.
    """
    if _SOURCE_OBJECT_PIN.get() is not None:
        raise RuntimeError("a global trademark source object pin is already armed")

    expected = _sha256(expected_sha256)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT jurisdiction, source_id, object_key, sha256
                FROM acquisition.global_trademark_source_object
                WHERE object_id = %s
                """,
                (object_id,),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"missing registered global trademark source object: {object_id}")
    if (
        row["jurisdiction"] != jurisdiction
        or row["source_id"] != source_id
        or row["object_key"] != object_key
        or str(row["sha256"]).lower() != expected
    ):
        raise RuntimeError(
            "registered global trademark source identity does not match the operator plan"
        )

    _SOURCE_OBJECT_PIN.set(
        SourceObjectPin(
            object_id=object_id,
            jurisdiction=jurisdiction,
            source_id=source_id,
            object_key=object_key,
            sha256=expected,
            path=path.resolve(),
        )
    )


def register_source_object(
    *,
    jurisdiction: str,
    source_id: str,
    path: Path,
    object_key: str | None = None,
    source_period_start: date | None = None,
    source_period_end: date | None = None,
    metadata: dict[str, Any] | None = None,
    precomputed_sha256: str | None = None,
) -> uuid.UUID:
    ensure_country_trademark_schemas()
    if source_period_start and source_period_end and source_period_end < source_period_start:
        raise ValueError("source_period_end cannot be before source_period_start")

    resolved_key = object_key or path.name
    pin = _SOURCE_OBJECT_PIN.get()
    if pin is not None:
        try:
            if (
                pin.jurisdiction != jurisdiction
                or pin.source_id != source_id
                or pin.object_key != resolved_key
                or pin.path != path.resolve()
            ):
                raise RuntimeError(
                    "loader source registration does not match the pinned operator source object"
                )
            actual = sha256_file(path)
            if actual != pin.sha256:
                raise RuntimeError(
                    "global trademark source bytes changed after preflight; refusing apply: "
                    f"expected_sha256={pin.sha256} actual_sha256={actual} path={path}"
                )
            _merge_source_metadata(pin.object_id, metadata)
            return pin.object_id
        finally:
            _SOURCE_OBJECT_PIN.set(None)

    checksum = (
        _sha256(precomputed_sha256)
        if precomputed_sha256 is not None
        else sha256_file(path)
    )
    object_id = source_object_id(
        source_id=source_id,
        object_key=resolved_key,
        sha256=checksum,
    )
    payload = json.dumps(metadata or {}, ensure_ascii=False)

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO acquisition.global_trademark_source_object (
                    object_id, jurisdiction, source_id, object_key, sha256,
                    source_period_start, source_period_end, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (source_id, object_key, sha256) DO UPDATE
                SET jurisdiction = EXCLUDED.jurisdiction,
                    source_period_start = COALESCE(
                        EXCLUDED.source_period_start,
                        acquisition.global_trademark_source_object.source_period_start
                    ),
                    source_period_end = COALESCE(
                        EXCLUDED.source_period_end,
                        acquisition.global_trademark_source_object.source_period_end
                    ),
                    metadata = acquisition.global_trademark_source_object.metadata || EXCLUDED.metadata
                RETURNING object_id
                """,
                (
                    object_id,
                    jurisdiction,
                    source_id,
                    resolved_key,
                    checksum,
                    source_period_start,
                    source_period_end,
                    payload,
                ),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        raise RuntimeError("failed to register global trademark source object")
    return row["object_id"]
