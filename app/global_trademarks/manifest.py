from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.db import postgres_conn
from app.global_trademarks.migrations import assert_global_trademark_schema


@dataclass(frozen=True, slots=True)
class SourceManifest:
    source_object_id: uuid.UUID
    source_sequence: int
    source_precedence: int
    expected_parts: int | None
    received_parts: int | None
    predecessor_object_key: str | None
    baseline_object_key: str | None
    parser_version: str
    mapping_version: str

    @property
    def parts_complete(self) -> bool | None:
        if self.expected_parts is None or self.received_parts is None:
            return None
        return self.received_parts == self.expected_parts

    def as_dict(self) -> dict[str, object]:
        return {
            "source_object_id": str(self.source_object_id),
            "source_sequence": self.source_sequence,
            "source_precedence": self.source_precedence,
            "expected_parts": self.expected_parts,
            "received_parts": self.received_parts,
            "parts_complete": self.parts_complete,
            "predecessor_object_key": self.predecessor_object_key,
            "baseline_object_key": self.baseline_object_key,
            "parser_version": self.parser_version,
            "mapping_version": self.mapping_version,
        }


def _validate_parts(expected_parts: int | None, received_parts: int | None) -> None:
    if expected_parts is not None and expected_parts < 0:
        raise ValueError("expected_parts must be non-negative")
    if received_parts is not None and received_parts < 0:
        raise ValueError("received_parts must be non-negative")
    if (
        expected_parts is not None
        and received_parts is not None
        and received_parts > expected_parts
    ):
        raise ValueError("received_parts cannot exceed expected_parts")


def upsert_source_manifest(
    *,
    source_object_id: uuid.UUID,
    source_sequence: int = 0,
    source_precedence: int = 0,
    expected_parts: int | None = None,
    received_parts: int | None = None,
    predecessor_object_key: str | None = None,
    baseline_object_key: str | None = None,
    parser_version: str = "",
    mapping_version: str = "",
) -> SourceManifest:
    assert_global_trademark_schema()
    if source_sequence < 0:
        raise ValueError("source_sequence must be non-negative")
    if source_precedence < 0:
        raise ValueError("source_precedence must be non-negative")
    _validate_parts(expected_parts, received_parts)

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO acquisition.global_trademark_source_manifest (
                    source_object_id, source_sequence, source_precedence,
                    expected_parts, received_parts, predecessor_object_key,
                    baseline_object_key, parser_version, mapping_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_object_id) DO UPDATE SET
                    source_sequence = EXCLUDED.source_sequence,
                    source_precedence = EXCLUDED.source_precedence,
                    expected_parts = EXCLUDED.expected_parts,
                    received_parts = EXCLUDED.received_parts,
                    predecessor_object_key = EXCLUDED.predecessor_object_key,
                    baseline_object_key = EXCLUDED.baseline_object_key,
                    parser_version = EXCLUDED.parser_version,
                    mapping_version = EXCLUDED.mapping_version,
                    updated_at = now()
                RETURNING source_object_id, source_sequence, source_precedence,
                    expected_parts, received_parts, predecessor_object_key,
                    baseline_object_key, parser_version, mapping_version
                """,
                (
                    source_object_id,
                    source_sequence,
                    source_precedence,
                    expected_parts,
                    received_parts,
                    predecessor_object_key,
                    baseline_object_key,
                    parser_version,
                    mapping_version,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise RuntimeError("failed to persist global trademark source manifest")
    return SourceManifest(**row)


def source_manifest(source_object_id: uuid.UUID) -> SourceManifest | None:
    assert_global_trademark_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_object_id, source_sequence, source_precedence,
                       expected_parts, received_parts, predecessor_object_key,
                       baseline_object_key, parser_version, mapping_version
                FROM acquisition.global_trademark_source_manifest
                WHERE source_object_id = %s
                """,
                (source_object_id,),
            )
            row = cur.fetchone()
    return SourceManifest(**row) if row else None
