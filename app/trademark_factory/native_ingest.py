from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.db import postgres_conn
from app.global_trademarks.ingest_runs import (
    begin_or_resume_ingest_run,
    checkpoint_ingest_run,
    complete_ingest_run,
    fail_ingest_run,
    ingest_run_id,
)
from app.global_trademarks.migrations import assert_global_trademark_schema
from app.trademark_factory.store_bundle import (
    NATIVE_STORE_BUNDLE_VERSION,
    NativeStoreBundle,
    append_native_record_bundle,
)
from app.trademark_factory.writer import Transform


NATIVE_INGEST_EXECUTOR_VERSION = "TRADEMARK_NATIVE_INGEST_EXECUTOR_V1"
_LINEAGE_METADATA_KEY = "native_ingest_lineage_sha256"


@dataclass(frozen=True, slots=True)
class NativeRecordEnvelope:
    """One deterministic source-native record emitted by a jurisdiction parser.

    ``source_index`` is a 1-based stable position inside the immutable source object. Parsers may
    replay from index 1 or resume directly at ``checkpoint + 1``; gaps and reordering fail closed.
    ``record_key`` remains jurisdiction/source-native identity and is never invented here.
    """

    source_index: int
    record_key: str
    native: Mapping[str, Any]
    source_payload: Mapping[str, object] | None = None

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.source_index < 1:
            errors.append("native record source_index must be >= 1")
        if not self.record_key.strip():
            errors.append("native record record_key must not be blank")
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class NativeIngestResult:
    run_id: uuid.UUID
    status: str
    processed_records: int
    inserted_observations: int
    replayed_observations: int
    checkpoint: int
    cumulative_records: int
    bounded: bool

    @property
    def complete(self) -> bool:
        return self.status in {"COMPLETE", "ALREADY_COMPLETE"}

    def as_dict(self) -> dict[str, object]:
        return {
            "executor_version": NATIVE_INGEST_EXECUTOR_VERSION,
            "run_id": str(self.run_id),
            "status": self.status,
            "processed_records": self.processed_records,
            "inserted_observations": self.inserted_observations,
            "replayed_observations": self.replayed_observations,
            "checkpoint": self.checkpoint,
            "cumulative_records": self.cumulative_records,
            "bounded": self.bounded,
        }


def _lineage_material(bundle: NativeStoreBundle, parser_version: str) -> dict[str, object]:
    return {
        "executor_version": NATIVE_INGEST_EXECUTOR_VERSION,
        "bundle_version": NATIVE_STORE_BUNDLE_VERSION,
        "jurisdiction": bundle.jurisdiction.strip().upper(),
        "source_id": bundle.source_id.strip(),
        "store_schema": bundle.store_schema.strip(),
        "parser_version": parser_version,
        "bindings": [
            {
                "binding_id": binding.binding_id,
                "schema_name": binding.spec.schema_name,
                "table_name": binding.spec.table_name,
                "domain": binding.spec.domain.value,
                "native_columns": [
                    {
                        "name": column.name,
                        "data_type": column.data_type.value,
                        "nullable": column.nullable,
                    }
                    for column in binding.spec.native_columns
                ],
                "mapping": binding.contract.as_dict(),
            }
            for binding in bundle.bindings
        ],
    }


def native_ingest_lineage_sha256(bundle: NativeStoreBundle, parser_version: str) -> str:
    if not parser_version.strip():
        raise ValueError("parser_version must not be blank")
    errors = bundle.validate()
    if errors:
        raise ValueError("; ".join(errors))
    encoded = json.dumps(
        _lineage_material(bundle, parser_version.strip()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_source_object_identity(
    *,
    source_object_id: uuid.UUID,
    bundle: NativeStoreBundle,
) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT jurisdiction, source_id
                FROM acquisition.global_trademark_source_object
                WHERE object_id = %s
                """,
                (source_object_id,),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"missing global trademark source object: {source_object_id}")
    if row["jurisdiction"].strip().upper() != bundle.jurisdiction.strip().upper():
        raise RuntimeError(
            "native ingest source jurisdiction does not match store bundle: "
            f"{row['jurisdiction']!r} != {bundle.jurisdiction!r}"
        )
    if row["source_id"].strip() != bundle.source_id.strip():
        raise RuntimeError(
            "native ingest source_id does not match store bundle: "
            f"{row['source_id']!r} != {bundle.source_id!r}"
        )


def _assert_existing_run_lineage(
    *,
    source_object_id: uuid.UUID,
    pipeline_id: str,
    lineage_sha256: str,
) -> None:
    run_id = ingest_run_id(source_object_id=source_object_id, pipeline_id=pipeline_id)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT metadata
                FROM acquisition.global_trademark_ingest_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()
    if not row:
        return
    existing = (row["metadata"] or {}).get(_LINEAGE_METADATA_KEY)
    if existing != lineage_sha256:
        raise RuntimeError(
            "native ingest pipeline lineage changed for an existing source run; "
            "use a new versioned pipeline_id instead of resuming with different parser/mapping/schema"
        )


def execute_native_ingest(
    *,
    source_object_id: uuid.UUID,
    pipeline_id: str,
    bundle: NativeStoreBundle,
    parser_version: str,
    records: Iterable[NativeRecordEnvelope],
    transforms: Mapping[str, Transform] | None = None,
    batch_size: int = 500,
    max_records: int | None = None,
) -> NativeIngestResult:
    """Durably map a deterministic source-native record stream into a native store bundle.

    This executor owns generic replay mechanics only: source identity verification, versioned
    pipeline lineage, durable checkpoint/resume, bounded pilots, atomic bundle writes and ingest-run
    status. It does not acquire source bytes, parse authority formats, create tables, decide current
    state, or infer legal/business semantics.
    """
    if not pipeline_id.strip():
        raise ValueError("pipeline_id must not be blank")
    if not parser_version.strip():
        raise ValueError("parser_version must not be blank")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if max_records is not None and max_records < 1:
        raise ValueError("max_records must be >= 1 when provided")
    errors = bundle.validate()
    if errors:
        raise ValueError("; ".join(errors))

    assert_global_trademark_schema()
    _assert_source_object_identity(source_object_id=source_object_id, bundle=bundle)
    lineage_sha256 = native_ingest_lineage_sha256(bundle, parser_version)
    _assert_existing_run_lineage(
        source_object_id=source_object_id,
        pipeline_id=pipeline_id.strip(),
        lineage_sha256=lineage_sha256,
    )

    state = begin_or_resume_ingest_run(
        source_object_id=source_object_id,
        jurisdiction=bundle.jurisdiction.strip().upper(),
        pipeline_id=pipeline_id.strip(),
        metadata={
            "executor_version": NATIVE_INGEST_EXECUTOR_VERSION,
            "bundle_version": NATIVE_STORE_BUNDLE_VERSION,
            "parser_version": parser_version.strip(),
            _LINEAGE_METADATA_KEY: lineage_sha256,
            "binding_ids": [binding.binding_id for binding in bundle.bindings],
            "mapping_versions": {
                binding.binding_id: binding.contract.version for binding in bundle.bindings
            },
        },
    )
    if state.complete:
        return NativeIngestResult(
            run_id=state.run_id,
            status="ALREADY_COMPLETE",
            processed_records=0,
            inserted_observations=0,
            replayed_observations=0,
            checkpoint=state.checkpoint,
            cumulative_records=state.rows_committed,
            bounded=max_records is not None,
        )

    processed = 0
    inserted_observations = 0
    replayed_observations = 0
    checkpoint = state.checkpoint
    cumulative = state.rows_committed
    last_seen_index: int | None = None
    exhausted = True

    try:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                for envelope in records:
                    envelope_errors = envelope.validate()
                    if envelope_errors:
                        raise ValueError("; ".join(envelope_errors))

                    if last_seen_index is None:
                        valid_starts = {1, state.checkpoint + 1}
                        if envelope.source_index not in valid_starts:
                            raise RuntimeError(
                                "native parser resume must begin at source index 1 or checkpoint + 1: "
                                f"checkpoint={state.checkpoint} first_index={envelope.source_index}"
                            )
                    elif envelope.source_index != last_seen_index + 1:
                        raise RuntimeError(
                            "native parser source_index sequence is not contiguous: "
                            f"previous={last_seen_index} current={envelope.source_index}"
                        )
                    last_seen_index = envelope.source_index

                    if envelope.source_index <= state.checkpoint:
                        continue
                    if envelope.source_index != checkpoint + 1:
                        raise RuntimeError(
                            "native parser replay order changed after durable checkpoint: "
                            f"checkpoint={checkpoint} next_index={envelope.source_index}"
                        )
                    if max_records is not None and processed >= max_records:
                        exhausted = False
                        break

                    append_result = append_native_record_bundle(
                        cur,
                        bundle,
                        native=envelope.native,
                        record_key=envelope.record_key,
                        source_object_id=source_object_id,
                        source_index=envelope.source_index,
                        parser_version=parser_version.strip(),
                        source_payload=envelope.source_payload,
                        transforms=transforms,
                    )
                    processed += 1
                    cumulative += 1
                    checkpoint = envelope.source_index
                    inserted_observations += append_result.inserted_count
                    replayed_observations += append_result.replay_count

                    if processed % batch_size == 0:
                        checkpoint_ingest_run(
                            cur,
                            run_id=state.run_id,
                            checkpoint=checkpoint,
                            rows_committed=cumulative,
                        )
                        conn.commit()

                if exhausted:
                    complete_ingest_run(
                        cur,
                        run_id=state.run_id,
                        checkpoint=checkpoint,
                        rows_committed=cumulative,
                    )
                    status = "COMPLETE"
                else:
                    checkpoint_ingest_run(
                        cur,
                        run_id=state.run_id,
                        checkpoint=checkpoint,
                        rows_committed=cumulative,
                    )
                    status = "PARTIAL"
                conn.commit()
    except Exception as exc:
        fail_ingest_run(run_id=state.run_id, error_text=str(exc))
        raise

    return NativeIngestResult(
        run_id=state.run_id,
        status=status,
        processed_records=processed,
        inserted_observations=inserted_observations,
        replayed_observations=replayed_observations,
        checkpoint=checkpoint,
        cumulative_records=cumulative,
        bounded=max_records is not None,
    )
