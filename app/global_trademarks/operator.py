from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.global_trademarks.manifest import (
    SourceManifest,
    attach_manifest_object,
    upsert_source_manifest,
)
from app.global_trademarks.migrations import assert_global_trademark_schema
from app.global_trademarks.preflight import SourcePreflight
from app.global_trademarks.source_objects import register_source_object


GLOBAL_TRADEMARK_OPERATOR_VERSION = "GLOBAL_TM_OPERATOR_V1"


@dataclass(frozen=True, slots=True)
class IngestPlan:
    command: str
    jurisdiction: str
    source_id: str
    path: Path
    object_key: str
    manifest_key: str
    source_period_start: date | None
    source_period_end: date | None
    source_sequence: int
    source_precedence: int
    expected_objects: int | None
    part_sequence: int | None
    predecessor_manifest_key: str | None
    baseline_manifest_key: str | None
    parser_version: str
    mapping_version: str
    preflight: SourcePreflight

    @property
    def execution_scope(self) -> str:
        # The execution lock follows the exact source bytes rather than the manifest
        # alias. The same object must not run concurrently merely because an operator
        # supplied a different release label for it.
        return ":".join(
            (
                "GLOBAL_TM",
                self.jurisdiction,
                self.source_id,
                self.object_key,
                self.preflight.sha256,
            )
        )

    def as_dict(self, *, apply_requested: bool) -> dict[str, object]:
        return {
            "operator_version": GLOBAL_TRADEMARK_OPERATOR_VERSION,
            "status": "READY_TO_APPLY" if self.preflight.schema_valid else "BLOCKED",
            "mutation": bool(apply_requested),
            "apply_required": True,
            "command": self.command,
            "jurisdiction": self.jurisdiction,
            "source_id": self.source_id,
            "path": str(self.path),
            "object_key": self.object_key,
            "manifest_key": self.manifest_key,
            "source_period_start": (
                self.source_period_start.isoformat() if self.source_period_start else None
            ),
            "source_period_end": self.source_period_end.isoformat() if self.source_period_end else None,
            "source_sequence": self.source_sequence,
            "source_precedence": self.source_precedence,
            "expected_objects": self.expected_objects,
            "part_sequence": self.part_sequence,
            "predecessor_manifest_key": self.predecessor_manifest_key,
            "baseline_manifest_key": self.baseline_manifest_key,
            "parser_version": self.parser_version,
            "mapping_version": self.mapping_version,
            "execution_scope": self.execution_scope,
            "preflight": self.preflight.as_dict(),
        }


def build_ingest_plan(
    *,
    command: str,
    jurisdiction: str,
    source_id: str,
    path: Path,
    preflight: SourcePreflight,
    object_key: str | None = None,
    manifest_key: str | None = None,
    source_period_start: date | None = None,
    source_period_end: date | None = None,
    source_sequence: int = 0,
    source_precedence: int = 0,
    expected_objects: int | None = 1,
    part_sequence: int | None = 1,
    predecessor_manifest_key: str | None = None,
    baseline_manifest_key: str | None = None,
    parser_version: str = "",
    mapping_version: str = "COUNTRY_NATIVE_V1",
) -> IngestPlan:
    jurisdiction = jurisdiction.strip().upper()
    source_id = source_id.strip()
    resolved_object_key = (object_key or path.name).strip()
    resolved_manifest_key = (manifest_key or resolved_object_key).strip()
    if not jurisdiction or not source_id or not resolved_object_key or not resolved_manifest_key:
        raise ValueError("jurisdiction, source_id, object_key and manifest_key are required")
    if source_period_start and source_period_end and source_period_end < source_period_start:
        raise ValueError("source_period_end cannot be before source_period_start")
    if source_sequence < 0 or source_precedence < 0:
        raise ValueError("source_sequence/source_precedence must be non-negative")
    if expected_objects is not None and expected_objects < 1:
        raise ValueError("expected_objects must be at least 1 when provided")
    if part_sequence is not None and part_sequence < 1:
        raise ValueError("part_sequence must be at least 1 when provided")
    if (
        expected_objects is not None
        and part_sequence is not None
        and part_sequence > expected_objects
    ):
        raise ValueError("part_sequence cannot exceed expected_objects")
    return IngestPlan(
        command=command,
        jurisdiction=jurisdiction,
        source_id=source_id,
        path=path,
        object_key=resolved_object_key,
        manifest_key=resolved_manifest_key,
        source_period_start=source_period_start,
        source_period_end=source_period_end,
        source_sequence=source_sequence,
        source_precedence=source_precedence,
        expected_objects=expected_objects,
        part_sequence=part_sequence,
        predecessor_manifest_key=predecessor_manifest_key,
        baseline_manifest_key=baseline_manifest_key,
        parser_version=parser_version,
        mapping_version=mapping_version,
        preflight=preflight,
    )


def register_plan_source(
    plan: IngestPlan,
    *,
    metadata: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, SourceManifest]:
    """Register one verified source object and attach it to its dataset manifest.

    Manifest attachment means the exact source object was received/registered. It does
    not mean parsing or ingestion succeeded; acceptance must additionally inspect the
    ingest-run ledger.
    """
    assert_global_trademark_schema()
    if not plan.preflight.schema_valid:
        raise RuntimeError("cannot register an ingest plan with invalid source preflight")
    source_object_id = register_source_object(
        jurisdiction=plan.jurisdiction,
        source_id=plan.source_id,
        path=plan.path,
        object_key=plan.object_key,
        source_period_start=plan.source_period_start,
        source_period_end=plan.source_period_end,
        metadata={
            "operator_version": GLOBAL_TRADEMARK_OPERATOR_VERSION,
            "manifest_key": plan.manifest_key,
            **(metadata or {}),
        },
    )
    manifest = upsert_source_manifest(
        jurisdiction=plan.jurisdiction,
        source_id=plan.source_id,
        manifest_key=plan.manifest_key,
        source_period_start=plan.source_period_start,
        source_period_end=plan.source_period_end,
        source_sequence=plan.source_sequence,
        source_precedence=plan.source_precedence,
        expected_objects=plan.expected_objects,
        predecessor_manifest_key=plan.predecessor_manifest_key,
        baseline_manifest_key=plan.baseline_manifest_key,
        parser_version=plan.parser_version,
        mapping_version=plan.mapping_version,
    )
    manifest = attach_manifest_object(
        manifest_id=manifest.manifest_id,
        source_object_id=source_object_id,
        part_sequence=plan.part_sequence,
    )
    return source_object_id, manifest
