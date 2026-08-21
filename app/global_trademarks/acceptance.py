from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from app.data_trust import DataTrustEvidence, DataTrustResult, evaluate_data_trust
from app.db import postgres_conn
from app.global_trademarks.catalog import SourceRole, country_plan
from app.global_trademarks.diagnostics import collect_readiness_audit
from app.global_trademarks.manifest import SourceManifest, source_manifest
from app.global_trademarks.migrations import global_trademark_migration_status


GLOBAL_TRADEMARK_ACCEPTANCE_VERSION = "GLOBAL_TM_ACCEPTANCE_V1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ManifestObjectEvidence:
    source_object_id: uuid.UUID
    object_key: str
    part_sequence: int | None
    jurisdiction: str
    source_id: str
    sha256: str
    complete_runs: int
    running_runs: int
    failed_runs: int

    @property
    def has_complete_run(self) -> bool:
        return self.complete_runs > 0

    @property
    def sha_verified(self) -> bool:
        return bool(_SHA256_RE.fullmatch(self.sha256.lower()))


@dataclass(frozen=True, slots=True)
class ManifestAcceptanceResult:
    manifest_id: uuid.UUID
    jurisdiction: str
    source_id: str
    manifest_key: str
    source_role: str
    authoritative_source: bool
    pipeline_ready: bool
    schema_ready: bool
    expected_objects: int | None
    attached_objects: int
    objects_complete: bool
    part_sequence_complete: bool
    source_identity_complete: bool
    sha_verified: bool
    complete_run_objects: int
    running_run_objects: int
    failed_run_objects: int
    missing_run_objects: int
    predecessor_resolved: bool
    baseline_resolved: bool
    source_period_start: date | None
    source_period_end: date | None
    release_accepted: bool
    reason_codes: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["acceptance_version"] = GLOBAL_TRADEMARK_ACCEPTANCE_VERSION
        payload["manifest_id"] = str(self.manifest_id)
        payload["source_period_start"] = (
            self.source_period_start.isoformat() if self.source_period_start else None
        )
        payload["source_period_end"] = (
            self.source_period_end.isoformat() if self.source_period_end else None
        )
        payload["reason_codes"] = list(self.reason_codes)
        payload["warnings"] = list(self.warnings)
        payload["jurisdiction_current_state_accepted"] = False
        payload["legal_conclusion"] = False
        return payload


def _source_spec(jurisdiction: str, source_id: str):
    try:
        plan = country_plan(jurisdiction)
    except ValueError:
        return None
    return next((source for source in plan.sources if source.source_id == source_id), None)


def _schema_ready(jurisdiction: str) -> bool:
    if not global_trademark_migration_status().ready:
        return False
    audit = collect_readiness_audit()
    match = next(
        (row for row in audit.jurisdictions if row.jurisdiction == jurisdiction),
        None,
    )
    return bool(match and match.schema_ready)


def _manifest_reference_resolved(
    manifest: SourceManifest,
    *,
    manifest_key: str | None,
    require_lower_sequence: bool,
) -> bool:
    if not manifest_key:
        return True
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT manifest_id, source_sequence
                FROM acquisition.global_trademark_manifest
                WHERE jurisdiction = %s AND source_id = %s AND manifest_key = %s
                """,
                (manifest.jurisdiction, manifest.source_id, manifest_key),
            )
            row = cur.fetchone()
    if not row or row["manifest_id"] == manifest.manifest_id:
        return False
    sequence = int(row["source_sequence"])
    if require_lower_sequence:
        return sequence < manifest.source_sequence
    return sequence <= manifest.source_sequence


def _object_evidence(manifest: SourceManifest) -> tuple[ManifestObjectEvidence, ...]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    o.source_object_id,
                    o.part_sequence,
                    s.object_key,
                    s.jurisdiction,
                    s.source_id,
                    s.sha256,
                    COUNT(r.run_id) FILTER (WHERE r.status = 'COMPLETE') AS complete_runs,
                    COUNT(r.run_id) FILTER (WHERE r.status = 'RUNNING') AS running_runs,
                    COUNT(r.run_id) FILTER (WHERE r.status = 'FAILED') AS failed_runs
                FROM acquisition.global_trademark_manifest_object AS o
                JOIN acquisition.global_trademark_source_object AS s
                  ON s.object_id = o.source_object_id
                LEFT JOIN acquisition.global_trademark_ingest_run AS r
                  ON r.source_object_id = o.source_object_id
                WHERE o.manifest_id = %s
                GROUP BY o.source_object_id, o.part_sequence, s.object_key,
                         s.jurisdiction, s.source_id, s.sha256
                ORDER BY o.part_sequence NULLS LAST, s.object_key, o.source_object_id
                """,
                (manifest.manifest_id,),
            )
            rows = cur.fetchall()
    return tuple(
        ManifestObjectEvidence(
            source_object_id=row["source_object_id"],
            object_key=row["object_key"],
            part_sequence=(
                int(row["part_sequence"]) if row["part_sequence"] is not None else None
            ),
            jurisdiction=row["jurisdiction"],
            source_id=row["source_id"],
            sha256=row["sha256"],
            complete_runs=int(row["complete_runs"] or 0),
            running_runs=int(row["running_runs"] or 0),
            failed_runs=int(row["failed_runs"] or 0),
        )
        for row in rows
    )


def evaluate_manifest_acceptance(manifest_id: uuid.UUID) -> ManifestAcceptanceResult:
    """Evaluate one source release without promoting it to jurisdiction-current truth.

    Acceptance here means that the declared release is structurally complete and its
    attached source objects have verified identities plus completed ingestion work. It
    never means the jurisdiction is current, legally complete, or safe for absence-based
    legal conclusions.
    """
    manifest = source_manifest(manifest_id)
    if manifest is None:
        raise ValueError(f"unknown global trademark manifest: {manifest_id}")

    source = _source_spec(manifest.jurisdiction, manifest.source_id)
    pipeline_ready = bool(source and source.pipeline_ready)
    authoritative = bool(source and source.authoritative)
    source_role = source.role.value if source else "UNCONFIGURED"
    schema_ready = _schema_ready(manifest.jurisdiction)
    objects = _object_evidence(manifest)

    expected = manifest.expected_objects
    objects_complete = bool(
        expected is not None and expected > 0 and len(objects) == expected
    )
    expected_sequences = list(range(1, expected + 1)) if expected else []
    actual_sequences = sorted(
        item.part_sequence for item in objects if item.part_sequence is not None
    )
    part_sequence_complete = bool(
        objects_complete
        and len(actual_sequences) == len(objects)
        and actual_sequences == expected_sequences
    )
    source_identity_complete = bool(
        objects
        and all(
            item.jurisdiction == manifest.jurisdiction
            and item.source_id == manifest.source_id
            for item in objects
        )
    )
    sha_verified = bool(objects and all(item.sha_verified for item in objects))
    complete_run_objects = sum(item.has_complete_run for item in objects)
    running_run_objects = sum(item.running_runs > 0 for item in objects)
    failed_run_objects = sum(item.failed_runs > 0 for item in objects)
    missing_run_objects = sum(not item.has_complete_run for item in objects)
    predecessor_resolved = _manifest_reference_resolved(
        manifest,
        manifest_key=manifest.predecessor_manifest_key,
        require_lower_sequence=True,
    )
    baseline_resolved = _manifest_reference_resolved(
        manifest,
        manifest_key=manifest.baseline_manifest_key,
        require_lower_sequence=False,
    )

    reasons: list[str] = []
    if source is None:
        reasons.append("SOURCE_NOT_CONFIGURED")
    elif not pipeline_ready:
        reasons.append("SOURCE_PIPELINE_NOT_READY")
    if not schema_ready:
        reasons.append("COUNTRY_SCHEMA_NOT_READY")
    if expected is None:
        reasons.append("EXPECTED_OBJECT_COUNT_UNKNOWN")
    elif expected <= 0:
        reasons.append("EXPECTED_OBJECT_COUNT_INVALID")
    if not objects_complete:
        reasons.append("MANIFEST_OBJECT_SET_INCOMPLETE")
    if not part_sequence_complete:
        reasons.append("MANIFEST_PART_SEQUENCE_INCOMPLETE")
    if not source_identity_complete:
        reasons.append("SOURCE_IDENTITY_MISMATCH")
    if not sha_verified:
        reasons.append("SOURCE_SHA256_NOT_VERIFIED")
    if running_run_objects:
        reasons.append("INGEST_RUN_STILL_RUNNING")
    if failed_run_objects:
        reasons.append("INGEST_RUN_FAILED")
    if missing_run_objects:
        reasons.append("SOURCE_OBJECT_WITHOUT_COMPLETE_INGEST_RUN")
    if not predecessor_resolved:
        reasons.append("PREDECESSOR_MANIFEST_UNRESOLVED")
    if not baseline_resolved:
        reasons.append("BASELINE_MANIFEST_UNRESOLVED")

    warnings: list[str] = [
        "MANIFEST_ACCEPTANCE_IS_NOT_JURISDICTION_CURRENT_STATE_ACCEPTANCE"
    ]
    if not authoritative:
        warnings.append("SOURCE_IS_NOT_AUTHORITATIVE")
    if source and source.role == SourceRole.HISTORICAL_SEED:
        warnings.append("HISTORICAL_SEED_CURRENT_STATE_NOT_VERIFIED")
    if manifest.source_period_end is None:
        warnings.append("SOURCE_COVERAGE_END_UNKNOWN")

    return ManifestAcceptanceResult(
        manifest_id=manifest.manifest_id,
        jurisdiction=manifest.jurisdiction,
        source_id=manifest.source_id,
        manifest_key=manifest.manifest_key,
        source_role=source_role,
        authoritative_source=authoritative,
        pipeline_ready=pipeline_ready,
        schema_ready=schema_ready,
        expected_objects=expected,
        attached_objects=len(objects),
        objects_complete=objects_complete,
        part_sequence_complete=part_sequence_complete,
        source_identity_complete=source_identity_complete,
        sha_verified=sha_verified,
        complete_run_objects=complete_run_objects,
        running_run_objects=running_run_objects,
        failed_run_objects=failed_run_objects,
        missing_run_objects=missing_run_objects,
        predecessor_resolved=predecessor_resolved,
        baseline_resolved=baseline_resolved,
        source_period_start=manifest.source_period_start,
        source_period_end=manifest.source_period_end,
        release_accepted=not reasons,
        reason_codes=tuple(reasons),
        warnings=tuple(warnings),
    )


def evaluate_manifest_data_trust(
    manifest_id: uuid.UUID,
    *,
    required_coverage_through: date | datetime | str | None,
) -> tuple[ManifestAcceptanceResult, DataTrustResult]:
    """Project release acceptance into the existing Data Trust contract.

    `source_supports_silence` is deliberately false in V1. A later jurisdiction-specific
    contract must explicitly prove that absence within verified source coverage carries
    a supported source meaning before Data Engine can expose trusted-for-silence=true.
    """
    acceptance = evaluate_manifest_acceptance(manifest_id)
    trust = evaluate_data_trust(
        DataTrustEvidence(
            domain=(
                f"GLOBAL_TM_MANIFEST:{acceptance.jurisdiction}:"
                f"{acceptance.source_id}:{acceptance.manifest_key}"
            ),
            query_plane_ready=acceptance.schema_ready,
            source_identity_complete=acceptance.source_identity_complete,
            registered_corpus_complete=(
                acceptance.objects_complete and acceptance.part_sequence_complete
            ),
            source_verification_passed=(
                acceptance.sha_verified
                and acceptance.pipeline_ready
                and acceptance.missing_run_objects == 0
                and acceptance.running_run_objects == 0
                and acceptance.failed_run_objects == 0
            ),
            acceptance_status="ACCEPTED" if acceptance.release_accepted else "NOT_ACCEPTED",
            coverage_through=acceptance.source_period_end,
            required_coverage_through=required_coverage_through,
            source_supports_silence=False,
            warnings=acceptance.warnings,
        )
    )
    return acceptance, trust
