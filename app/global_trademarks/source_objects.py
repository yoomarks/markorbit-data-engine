from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from app.db import postgres_conn
from app.global_trademarks.schema import ensure_country_trademark_schemas


_SOURCE_NAMESPACE = uuid.UUID("c37b1f26-5d5a-4afb-9708-a601903b1ad1")


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def source_object_id(*, source_id: str, object_key: str, sha256: str) -> uuid.UUID:
    material = f"{source_id}\0{object_key}\0{sha256}"
    return uuid.uuid5(_SOURCE_NAMESPACE, material)


def register_source_object(
    *,
    jurisdiction: str,
    source_id: str,
    path: Path,
    object_key: str | None = None,
    source_period_start: date | None = None,
    source_period_end: date | None = None,
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID:
    ensure_country_trademark_schemas()
    if source_period_start and source_period_end and source_period_end < source_period_start:
        raise ValueError("source_period_end cannot be before source_period_start")

    resolved_key = object_key or path.name
    checksum = sha256_file(path)
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
