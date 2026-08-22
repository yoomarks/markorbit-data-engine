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
    get_ingest_run_state,
)
from app.global_trademarks.migrations import assert_global_trademark_schema
from app.trademark_factory.store_bundle import NativeStoreBundle, append_native_record_bundle


NATIVE_INGEST_RUNNER_VERSION = "TRADEMARK_NATIVE_INGEST_RUNNER_V1"
_CONTRACT_METADATA_KEY = "native_ingest_contract_hash"
_RUNNER_METADATA_KEY = "native_ingest_runner_version"


@dataclass(frozen=True, slots=True)
class NativeRecordEnvelope:
    """One parser-produced source-native record in deterministic logical order.

    `source_index` is a contiguous, 1-based logical record sequence for the selected source
    pipeline. It is intentionally not a byte offset, database identity, legal status, or a
    globally invented trademark key. The jurisdiction parser owns `record_key`.
    """

    source_index: int
    record_key: str
    native: Mapping[str, Any]
    source_payload: Mapping[str, object] | None = None

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.source_index < 1:
            errors.append("source_index must be >= 1")
        if not self.record_key.strip():
            errors.append("record_key must not be blank")
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class NativeIngestResult:
    run_id: uuid.UUID
    status: str
    processed_records: int
    cumulative_records: int
    inserted_observations: int
    replay_observations: int
    checkpoint: int
    contract_hash: str

    @property
    def complete(self) -> bool:
        return self.status == "COMPLETE"

    def as_dict(self) -> dict[str, object]:
        return {
            "native_ingest_runner_version": NATIVE_INGEST_RUNNER_VERSION,
            "run_id": str(self.run_id),
            "status": self.status,
            "processed_records": self.processed_records,
            "cumulative_records": self.cumulative_records,
            "inserted_observations": self.inserted_observations,
            "replay_observations": self.replay_observations,
            "checkpoint": self.checkpoint,
            "contract_hash": self.contract_hash,
        }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def native_ingest_contract_hash(
    *,
    bundle: NativeStoreBundle,
    pipeline_id: str,
    parser_version: str,
) -> str:
    """Fingerprint the reviewed parser/mapping/native-table contract for durable resume safety."""
    material = {
        "runner_version": NATIVE_INGEST_RUNNER_VERSION,
        "jurisdiction": bundle.jurisdiction.strip().upper(),
        "source_id": bundle.source_id,
        "pipeline_id": pipeline_id,
        "parser_version": parser_version,
        "store_schema": bundle.store_schema,
        "bindings": [
            {
                "binding_id": binding.binding_id,
                "table": binding.spec.qualified_name,
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
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _source_object_identity(source_object_id: uuid.UUID) -> tuple[str, str]:
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
        raise ValueError(f"unknown source object: {source_object_id}")
    return str(row["jurisdiction"]).upper(), str(row["source_id"])


def _existing_run_metadata(
    *,
    source_object_id: uuid.UUID,
    pipeline_id: str,
) -> Mapping[str, object] | None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT metadata
                FROM acquisition.global_trademark_ingest_run
                WHERE source_object_id = %s AND pipeline_id = %s
                """,
                (source_object_id, pipeline_id),
            )
            row = cur.fetchone()
    return None if not row else dict(row["metadata"] or {})


def _validate_run_contract(
    *,
    source_object_id: uuid.UUID,
    bundle: NativeStoreBundle,
    pipeline_id: str,
    contract_hash: str,
) -> None:
    jurisdiction, source_id = _source_object_identity(source_object_id)
    if jurisdiction != bundle.jurisdiction.strip().upper():
        raise ValueError(
            f"source object jurisdiction {jurisdiction!r} does not match bundle "
            f"{bundle.jurisdiction!r}"
        )
    if source_id != bundle.source_id:
        raise ValueError(
            f"source object source_id {source_id!r} does not match bundle {bundle.source_id!r}"
        )

    metadata = _existing_run_metadata(
        source_object_id=source_object_id,
        pipeline_id=pipeline_id,
    )
    if metadata is None:
        return
    existing_hash = metadata.get(_CONTRACT_METADATA_KEY)
    existing_runner = metadata.get(_RUNNER_METADATA_KEY)
    if existing_hash is None or existing_runner is None:
        raise RuntimeError(
            "existing ingest run is not owned by the reusable native ingest runner; "
            "choose a distinct versioned pipeline_id"
        )
    if existing_runner != NATIVE_INGEST_RUNNER_VERSION:
        raise RuntimeError(
            f"native ingest runner version changed for existing run: {existing_runner!r}"
        )
    if existing_hash != contract_hash:
        raise RuntimeError(
            "native ingest contract changed for existing source/pipeline; bump parser/mapping "
            "and durable pipeline identity instead of resuming under different semantics"
        )


def _next_new_record(
    iterator,
    *,
    checkpoint: int,
    previous_index: int,
) -> tuple[NativeRecordEnvelope | None, int]:
    while True:
        try:
            record = next(iterator)
        except StopIteration:
            return None, previous_index
        errors = record.validate()
        if errors:
            raise ValueError("; ".join(errors))
        if record.source_index <= previous_index:
            raise RuntimeError(
                "native parser source_index must be strictly increasing for deterministic resume"
            )
        previous_index = record.source_index
        if record.source_index <= checkpoint:
            continue
        expected = checkpoint + 1
        if record.source_index != expected:
            raise RuntimeError(
                f"native parser resume gap: expected source_index {expected}, "
                f"got {record.source_index}"
            )
        return record, previous_index


def run_native_ingest(
    *,
    source_object_id: uuid.UUID,
    bundle: NativeStoreBundle,
    pipeline_id: str,
    parser_version: str,
    records: Iterable[NativeRecordEnvelope],
    batch_size: int = 500,
    max_records: int | None = None,
    metadata: Mapping[str, object] | None = None,
) -> NativeIngestResult:
    """Durably ingest a deterministic source-native record stream through a native-store bundle.

    The runner owns batching/checkpoint/resume only. It does not acquire source bytes, create source
    objects/manifests, install DDL, infer record identity, choose current-state winners, or make legal
    conclusions. `source_object_id` must already be registered by the shared operator path and the
    native tables must already exist through an explicit migration action.
    """
    assert_global_trademark_schema()
    bundle_errors = bundle.validate()
    if bundle_errors:
        raise ValueError("; ".join(bundle_errors))
    if not pipeline_id.strip():
        raise ValueError("pipeline_id is required")
    if not parser_version.strip():
        raise ValueError("parser_version is required")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if max_records is not None and max_records < 1:
        raise ValueError("max_records must be >= 1 when provided")

    contract_hash = native_ingest_contract_hash(
        bundle=bundle,
        pipeline_id=pipeline_id,
        parser_version=parser_version,
    )
    _validate_run_contract(
        source_object_id=source_object_id,
        bundle=bundle,
        pipeline_id=pipeline_id,
        contract_hash=contract_hash,
    )

    run_metadata = dict(metadata or {})
    forbidden = {_CONTRACT_METADATA_KEY, _RUNNER_METADATA_KEY} & set(run_metadata)
    if forbidden:
        raise ValueError(
            "reserved native ingest metadata keys: " + ", ".join(sorted(forbidden))
        )
    run_metadata[_CONTRACT_METADATA_KEY] = contract_hash
    run_metadata[_RUNNER_METADATA_KEY] = NATIVE_INGEST_RUNNER_VERSION
    run_metadata["source_id"] = bundle.source_id
    run_metadata["store_schema"] = bundle.store_schema
    run_metadata["parser_version"] = parser_version

    state = begin_or_resume_ingest_run(
        source_object_id=source_object_id,
        jurisdiction=bundle.jurisdiction.strip().upper(),
        pipeline_id=pipeline_id,
        metadata=run_metadata,
    )
    if state.complete:
        return NativeIngestResult(
            run_id=state.run_id,
            status="COMPLETE",
            processed_records=0,
            cumulative_records=state.rows_committed,
            inserted_observations=0,
            replay_observations=0,
            checkpoint=state.checkpoint,
            contract_hash=contract_hash,
        )

    iterator = iter(records)
    previous_index = 0
    checkpoint = state.checkpoint
    cumulative = state.rows_committed
    processed = 0
    inserted_observations = 0
    replay_observations = 0

    try:
        while True:
            batch: list[NativeRecordEnvelope] = []
            while len(batch) < batch_size and (
                max_records is None or processed + len(batch) < max_records
            ):
                record, previous_index = _next_new_record(
                    iterator,
                    checkpoint=checkpoint + len(batch),
                    previous_index=previous_index,
                )
                if record is None:
                    break
                batch.append(record)

            if batch:
                with postgres_conn() as conn:
                    try:
                        with conn.cursor() as cur:
                            batch_inserted = 0
                            batch_replayed = 0
                            for record in batch:
                                result = append_native_record_bundle(
                                    cur,
                                    bundle,
                                    native=record.native,
                                    record_key=record.record_key,
                                    source_object_id=source_object_id,
                                    source_index=record.source_index,
                                    parser_version=parser_version,
                                    source_payload=record.source_payload,
                                )
                                batch_inserted += result.inserted_count
                                batch_replayed += result.replay_count
                            next_checkpoint = batch[-1].source_index
                            next_cumulative = cumulative + len(batch)
                            checkpoint_ingest_run(
                                cur,
                                run_id=state.run_id,
                                checkpoint=next_checkpoint,
                                rows_committed=next_cumulative,
                            )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                checkpoint = batch[-1].source_index
                cumulative += len(batch)
                processed += len(batch)
                inserted_observations += batch_inserted
                replay_observations += batch_replayed

            if max_records is not None and processed >= max_records:
                lookahead, previous_index = _next_new_record(
                    iterator,
                    checkpoint=checkpoint,
                    previous_index=previous_index,
                )
                if lookahead is not None:
                    return NativeIngestResult(
                        run_id=state.run_id,
                        status="PARTIAL",
                        processed_records=processed,
                        cumulative_records=cumulative,
                        inserted_observations=inserted_observations,
                        replay_observations=replay_observations,
                        checkpoint=checkpoint,
                        contract_hash=contract_hash,
                    )

            if not batch or (max_records is not None and processed >= max_records):
                with postgres_conn() as conn:
                    with conn.cursor() as cur:
                        complete_ingest_run(
                            cur,
                            run_id=state.run_id,
                            checkpoint=checkpoint,
                            rows_committed=cumulative,
                        )
                    conn.commit()
                return NativeIngestResult(
                    run_id=state.run_id,
                    status="COMPLETE",
                    processed_records=processed,
                    cumulative_records=cumulative,
                    inserted_observations=inserted_observations,
                    replay_observations=replay_observations,
                    checkpoint=checkpoint,
                    contract_hash=contract_hash,
                )
    except Exception as exc:
        fail_ingest_run(run_id=state.run_id, error_text=str(exc))
        raise
