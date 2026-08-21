from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from app.db import postgres_conn
from app.global_trademarks.migrations import assert_global_trademark_schema


_MANIFEST_NAMESPACE = uuid.UUID("77d1be0d-3f8e-4d73-a94e-1a07c3f0c9b4")


@dataclass(frozen=True, slots=True)
class SourceManifest:
    manifest_id: uuid.UUID
    jurisdiction: str
    source_id: str
    manifest_key: str
    source_period_start: date | None
    source_period_end: date | None
    source_sequence: int
    source_precedence: int
    expected_objects: int | None
    attached_objects: int
    predecessor_manifest_key: str | None
    baseline_manifest_key: str | None
    parser_version: str
    mapping_version: str

    @property
    def objects_complete(self) -> bool | None:
        if self.expected_objects is None:
            return None
        return self.attached_objects == self.expected_objects

    def as_dict(self) -> dict[str, object]:
        return {
            "manifest_id": str(self.manifest_id),
            "jurisdiction": self.jurisdiction,
            "source_id": self.source_id,
            "manifest_key": self.manifest_key,
            "source_period_start": (
                self.source_period_start.isoformat() if self.source_period_start else None
            ),
            "source_period_end": self.source_period_end.isoformat() if self.source_period_end else None,
            "source_sequence": self.source_sequence,
            "source_precedence": self.source_precedence,
            "expected_objects": self.expected_objects,
            "attached_objects": self.attached_objects,
            "objects_complete": self.objects_complete,
            "predecessor_manifest_key": self.predecessor_manifest_key,
            "baseline_manifest_key": self.baseline_manifest_key,
            "parser_version": self.parser_version,
            "mapping_version": self.mapping_version,
        }


def source_manifest_id(*, jurisdiction: str, source_id: str, manifest_key: str) -> uuid.UUID:
    material = "\0".join(
        (jurisdiction.strip().upper(), source_id.strip(), manifest_key.strip())
    )
    if not all(material.split("\0")):
        raise ValueError("jurisdiction, source_id and manifest_key are required")
    return uuid.uuid5(_MANIFEST_NAMESPACE, material)


def _assert_manifest_compatible(
    row: dict[str, object],
    *,
    source_period_start: date | None,
    source_period_end: date | None,
    source_sequence: int,
    source_precedence: int,
    expected_objects: int | None,
    predecessor_manifest_key: str | None,
    baseline_manifest_key: str | None,
    parser_version: str,
    mapping_version: str,
) -> None:
    checks = (
        ("source_period_start", source_period_start, False),
        ("source_period_end", source_period_end, False),
        ("source_sequence", source_sequence, True),
        ("source_precedence", source_precedence, True),
        ("expected_objects", expected_objects, False),
        ("predecessor_manifest_key", predecessor_manifest_key, False),
        ("baseline_manifest_key", baseline_manifest_key, False),
        ("parser_version", parser_version or None, False),
        ("mapping_version", mapping_version or None, False),
    )
    conflicts: list[str] = []
    for field, requested, always_assert in checks:
        if requested is None and not always_assert:
            continue
        existing = row[field]
        if existing in (None, ""):
            continue
        if existing != requested:
            conflicts.append(f"{field}: existing={existing!r}, requested={requested!r}")
    if conflicts:
        raise ValueError(
            "global trademark manifest metadata conflict; use a different manifest_key "
            "instead of rewriting an existing source release: " + "; ".join(conflicts)
        )


def upsert_source_manifest(
    *,
    jurisdiction: str,
    source_id: str,
    manifest_key: str,
    source_period_start: date | None = None,
    source_period_end: date | None = None,
    source_sequence: int = 0,
    source_precedence: int = 0,
    expected_objects: int | None = None,
    predecessor_manifest_key: str | None = None,
    baseline_manifest_key: str | None = None,
    parser_version: str = "",
    mapping_version: str = "",
) -> SourceManifest:
    assert_global_trademark_schema()
    jurisdiction = jurisdiction.strip().upper()
    source_id = source_id.strip()
    manifest_key = manifest_key.strip()
    if not jurisdiction or not source_id or not manifest_key:
        raise ValueError("jurisdiction, source_id and manifest_key are required")
    if source_sequence < 0:
        raise ValueError("source_sequence must be non-negative")
    if source_precedence < 0:
        raise ValueError("source_precedence must be non-negative")
    if expected_objects is not None and expected_objects < 0:
        raise ValueError("expected_objects must be non-negative")
    if source_period_start and source_period_end and source_period_end < source_period_start:
        raise ValueError("source_period_end cannot be before source_period_start")

    manifest_id = source_manifest_id(
        jurisdiction=jurisdiction,
        source_id=source_id,
        manifest_key=manifest_key,
    )
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO acquisition.global_trademark_manifest (
                    manifest_id, jurisdiction, source_id, manifest_key,
                    source_period_start, source_period_end, source_sequence,
                    source_precedence, expected_objects, predecessor_manifest_key,
                    baseline_manifest_key, parser_version, mapping_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (jurisdiction, source_id, manifest_key) DO NOTHING
                """,
                (
                    manifest_id,
                    jurisdiction,
                    source_id,
                    manifest_key,
                    source_period_start,
                    source_period_end,
                    source_sequence,
                    source_precedence,
                    expected_objects,
                    predecessor_manifest_key,
                    baseline_manifest_key,
                    parser_version,
                    mapping_version,
                ),
            )
            cur.execute(
                """
                SELECT manifest_id, source_period_start, source_period_end,
                       source_sequence, source_precedence, expected_objects,
                       predecessor_manifest_key, baseline_manifest_key,
                       parser_version, mapping_version
                FROM acquisition.global_trademark_manifest
                WHERE jurisdiction = %s AND source_id = %s AND manifest_key = %s
                FOR UPDATE
                """,
                (jurisdiction, source_id, manifest_key),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("failed to persist global trademark source manifest")
            _assert_manifest_compatible(
                row,
                source_period_start=source_period_start,
                source_period_end=source_period_end,
                source_sequence=source_sequence,
                source_precedence=source_precedence,
                expected_objects=expected_objects,
                predecessor_manifest_key=predecessor_manifest_key,
                baseline_manifest_key=baseline_manifest_key,
                parser_version=parser_version,
                mapping_version=mapping_version,
            )
            cur.execute(
                """
                UPDATE acquisition.global_trademark_manifest
                SET source_period_start = COALESCE(source_period_start, %s),
                    source_period_end = COALESCE(source_period_end, %s),
                    expected_objects = COALESCE(expected_objects, %s),
                    predecessor_manifest_key = COALESCE(predecessor_manifest_key, %s),
                    baseline_manifest_key = COALESCE(baseline_manifest_key, %s),
                    parser_version = CASE WHEN parser_version = '' THEN %s ELSE parser_version END,
                    mapping_version = CASE WHEN mapping_version = '' THEN %s ELSE mapping_version END,
                    updated_at = now()
                WHERE manifest_id = %s
                """,
                (
                    source_period_start,
                    source_period_end,
                    expected_objects,
                    predecessor_manifest_key,
                    baseline_manifest_key,
                    parser_version,
                    mapping_version,
                    row["manifest_id"],
                ),
            )
        conn.commit()
    result = source_manifest(manifest_id)
    if result is None:
        raise RuntimeError("persisted global trademark source manifest is not readable")
    return result


def attach_manifest_object(
    *,
    manifest_id: uuid.UUID,
    source_object_id: uuid.UUID,
    part_sequence: int | None = None,
    object_role: str = "PRIMARY",
) -> SourceManifest:
    assert_global_trademark_schema()
    if part_sequence is not None and part_sequence < 1:
        raise ValueError("part_sequence must be at least 1")
    role = object_role.strip().upper()
    if not role:
        raise ValueError("object_role is required")

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO acquisition.global_trademark_manifest_object (
                    manifest_id, source_object_id, part_sequence, object_role
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (manifest_id, source_object_id) DO UPDATE SET
                    part_sequence = COALESCE(
                        EXCLUDED.part_sequence,
                        acquisition.global_trademark_manifest_object.part_sequence
                    ),
                    object_role = EXCLUDED.object_role
                """,
                (manifest_id, source_object_id, part_sequence, role),
            )
        conn.commit()
    result = source_manifest(manifest_id)
    if result is None:
        raise RuntimeError(f"missing global trademark manifest after attach: {manifest_id}")
    return result


def source_manifest(manifest_id: uuid.UUID) -> SourceManifest | None:
    assert_global_trademark_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.manifest_id, m.jurisdiction, m.source_id, m.manifest_key,
                       m.source_period_start, m.source_period_end, m.source_sequence,
                       m.source_precedence, m.expected_objects,
                       m.predecessor_manifest_key, m.baseline_manifest_key,
                       m.parser_version, m.mapping_version,
                       COUNT(o.source_object_id)::bigint AS attached_objects
                FROM acquisition.global_trademark_manifest AS m
                LEFT JOIN acquisition.global_trademark_manifest_object AS o
                  ON o.manifest_id = m.manifest_id
                WHERE m.manifest_id = %s
                GROUP BY m.manifest_id
                """,
                (manifest_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return SourceManifest(
        manifest_id=row["manifest_id"],
        jurisdiction=row["jurisdiction"],
        source_id=row["source_id"],
        manifest_key=row["manifest_key"],
        source_period_start=row["source_period_start"],
        source_period_end=row["source_period_end"],
        source_sequence=int(row["source_sequence"]),
        source_precedence=int(row["source_precedence"]),
        expected_objects=(
            int(row["expected_objects"]) if row["expected_objects"] is not None else None
        ),
        attached_objects=int(row["attached_objects"]),
        predecessor_manifest_key=row["predecessor_manifest_key"],
        baseline_manifest_key=row["baseline_manifest_key"],
        parser_version=row["parser_version"],
        mapping_version=row["mapping_version"],
    )
