from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import time
from typing import Any, Callable
import uuid

from app.contact_ingest import country_inference as engine
from app.db import postgres_conn
from app.work_engine import DurableWorkUnitStore, WorkUnitSpec


CHECKPOINT_VERSION = "CONTACT_COUNTRY_INFERENCE_WORK_V1"
WORK_OWNER_SCOPE = "CONTACT_COUNTRY_INFERENCE"
TASK_GROUP = "COUNTRY_INFERENCE_ENTITY_RANGE"
PARTITION_KIND = "ENTITY_RANGE"

_WORK_SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.country_inference_work_unit (
    run_id uuid NOT NULL
        REFERENCES contact.country_inference_run(run_id) ON DELETE CASCADE,
    checkpoint_version text NOT NULL,
    task_key char(64) NOT NULL,
    task_group text NOT NULL,
    task_index integer NOT NULL,
    task_total integer NOT NULL,
    partition_kind text NOT NULL,
    range_lower uuid,
    range_upper uuid,
    operation_hash char(64) NOT NULL,
    item_count integer NOT NULL,
    status text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    started_at timestamptz,
    completed_at timestamptz,
    last_error text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, checkpoint_version, task_key),
    CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    CHECK (task_index >= 1),
    CHECK (task_total >= task_index),
    CHECK (item_count >= 1),
    CHECK (partition_kind = 'ENTITY_RANGE'),
    CHECK (range_lower IS NOT NULL),
    CHECK (range_upper IS NOT NULL),
    CHECK (range_lower <= range_upper)
);

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_work_status
ON contact.country_inference_work_unit(run_id, checkpoint_version, status, task_index);

CREATE INDEX IF NOT EXISTS ix_contact_country_inference_last_run
ON contact.entity_country_inference(last_run_id, entity_id);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _normalize_run_id(run_id: str) -> str:
    return str(uuid.UUID(str(run_id)))


def operation_hash(
    *,
    apply: bool,
    min_confidence: float,
    min_margin: float,
    batch_size: int,
    max_entities: int | None,
) -> str:
    payload = {
        "rule_version": engine.COUNTRY_INFERENCE_VERSION,
        "apply": bool(apply),
        "min_confidence": float(min_confidence),
        "min_margin": float(min_margin),
        "batch_size": int(batch_size),
        "max_entities": int(max_entities) if max_entities is not None else None,
        "partition_kind": PARTITION_KIND,
    }
    return sha256(_json(payload).encode("utf-8")).hexdigest()


def ensure_country_inference_work_schema() -> None:
    engine.ensure_country_inference_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_WORK_SCHEMA_SQL)
        conn.commit()


def _read_run(run_id: str) -> dict[str, Any] | None:
    normalized = _normalize_run_id(run_id)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id::text, rule_version, status, apply_mode,
                       min_confidence, min_margin, batch_size, metrics,
                       error_message, started_at, finished_at
                FROM contact.country_inference_run
                WHERE run_id = %s::uuid
                """,
                (normalized,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def _run_metrics(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metrics") or {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        parsed = json.loads(raw)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _stored_max_entities(row: dict[str, Any]) -> int | None:
    raw = _run_metrics(row).get("max_entities")
    if raw in (None, ""):
        return None
    return int(raw)


def _unfinished_work_run() -> str | None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.run_id::text
                FROM contact.country_inference_run AS r
                WHERE r.status IN ('RUNNING', 'FAILED')
                  AND EXISTS (
                      SELECT 1
                      FROM contact.country_inference_work_unit AS w
                      WHERE w.run_id = r.run_id
                        AND w.checkpoint_version = %s
                  )
                ORDER BY r.started_at DESC, r.run_id DESC
                LIMIT 1
                """,
                (CHECKPOINT_VERSION,),
            )
            row = cur.fetchone()
    return str(row["run_id"]) if row else None


def _create_run(
    *,
    apply: bool,
    min_confidence: float,
    min_margin: float,
    batch_size: int,
    max_entities: int | None,
) -> str:
    run_id = str(uuid.uuid4())
    metrics = {
        "event": "CONTACT_COUNTRY_INFERENCE_START",
        "run_id": run_id,
        "status": "RUNNING",
        "apply": bool(apply),
        "max_entities": max_entities,
        "work_engine": {
            "owner_scope": WORK_OWNER_SCOPE,
            "checkpoint_version": CHECKPOINT_VERSION,
            "partition_kind": PARTITION_KIND,
        },
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO contact.country_inference_run(
                    run_id, rule_version, status, apply_mode,
                    min_confidence, min_margin, batch_size, metrics
                ) VALUES (%s, %s, 'RUNNING', %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    run_id,
                    engine.COUNTRY_INFERENCE_VERSION,
                    apply,
                    min_confidence,
                    min_margin,
                    batch_size,
                    _json(metrics),
                ),
            )
        conn.commit()
    return run_id


def _set_run_running(run_id: str) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE contact.country_inference_run
                SET status = 'RUNNING',
                    error_message = NULL,
                    finished_at = NULL,
                    metrics = COALESCE(metrics, '{}'::jsonb)
                              || '{"status":"RUNNING","resumed":true}'::jsonb
                WHERE run_id = %s::uuid
                """,
                (_normalize_run_id(run_id),),
            )
        conn.commit()


def work_summary_for_run(run_id: str) -> dict[str, int]:
    ensure_country_inference_work_schema()
    store = CountryInferenceWorkStore(
        run_id,
        operation_hash_value=None,
        allow_unknown_operation=True,
    )
    return store.summary()


class CountryInferenceWorkStore:
    """Contact Country Inference persistence adapter for the generic Work Engine.

    The inference run UUID is the durable ``job_id``. Each committed contact batch is
    one ``ENTITY_RANGE`` work unit. The adapter keeps the domain table independent from
    CN while sharing the same RUNNING/SUCCESS/FAILED transition semantics.
    """

    def __init__(
        self,
        run_id: str,
        *,
        operation_hash_value: str | None,
        allow_unknown_operation: bool = False,
    ) -> None:
        self.run_id = _normalize_run_id(run_id)
        self.operation_hash = str(operation_hash_value or "")
        if not self.operation_hash and not allow_unknown_operation:
            raise ValueError("operation_hash_value is required")
        self._pending_item_counts: dict[str, int] = {}
        self._work_store = DurableWorkUnitStore(
            owner_scope=WORK_OWNER_SCOPE,
            job_id=self.run_id,
            checkpoint_version=CHECKPOINT_VERSION,
            read_task=self._read_task,
            upsert_running=self._upsert_running,
            set_success=self._set_success,
            set_failed=self._set_failed,
            summarize=self._summarize,
        )

    def _assert_job_id(self, job_id: str) -> None:
        if _normalize_run_id(job_id) != self.run_id:
            raise RuntimeError("contact country work-unit job scope disagrees with run_id")

    def task_key(self, *, lower: str, upper: str) -> str:
        return self._work_store.task_key(
            operation_hash=self.operation_hash,
            partition_kind=PARTITION_KIND,
            lower=lower,
            upper=upper,
        )

    def mark_running(
        self,
        *,
        task_key: str,
        task_index: int,
        lower: str,
        upper: str,
        item_count: int,
    ) -> None:
        if item_count < 1:
            raise ValueError("item_count must be positive")
        self._pending_item_counts[task_key] = int(item_count)
        try:
            self._work_store.mark_running(
                task_key=task_key,
                task_group=TASK_GROUP,
                task_index=task_index,
                task_total=task_index,
                partition_kind=PARTITION_KIND,
                lower=lower,
                upper=upper,
                operation_hash=self.operation_hash,
            )
        finally:
            self._pending_item_counts.pop(task_key, None)

    def mark_success(self, task_key: str) -> None:
        self._work_store.mark_success(task_key)

    def mark_failed(self, task_key: str, error: str) -> None:
        self._work_store.mark_failed(task_key, error)

    def summary(self) -> dict[str, int]:
        return self._work_store.summary()

    def assert_complete(self) -> dict[str, int]:
        return self._work_store.assert_complete()

    def rows(self) -> list[dict[str, Any]]:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT task_key, task_group, task_index, task_total,
                           partition_kind, range_lower::text, range_upper::text,
                           operation_hash, item_count, status, attempts,
                           started_at, completed_at, last_error, updated_at
                    FROM contact.country_inference_work_unit
                    WHERE run_id = %s::uuid AND checkpoint_version = %s
                    ORDER BY task_index, task_key
                    """,
                    (self.run_id, CHECKPOINT_VERSION),
                )
                return [dict(row) for row in cur.fetchall()]

    def artifact_count(self, row: dict[str, Any]) -> int:
        lower = str(row.get("range_lower") or "")
        upper = str(row.get("range_upper") or "")
        if not lower or not upper:
            raise RuntimeError("contact country work unit is missing entity range")
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*) AS row_count
                    FROM contact.entity_country_inference
                    WHERE last_run_id = %s::uuid
                      AND entity_id >= %s::uuid
                      AND entity_id <= %s::uuid
                    """,
                    (self.run_id, lower, upper),
                )
                result = cur.fetchone()
        return int(result["row_count"] or 0)

    def reconcile_committed_units(self) -> int:
        """Promote a unit when its atomic result transaction committed first.

        The inference rows for one batch are committed in one PostgreSQL transaction.
        Therefore an interrupted unit may validly have either zero current-run rows or
        exactly ``item_count`` rows. Any partial count is an integrity failure and must
        not be guessed through.
        """
        repaired = 0
        for row in self.rows():
            row_hash = str(row.get("operation_hash") or "")
            if self.operation_hash and row_hash != self.operation_hash:
                raise RuntimeError("contact country work-unit operation hash drift")
            if str(row.get("status") or "") == "SUCCESS":
                continue
            item_count = int(row.get("item_count") or 0)
            actual = self.artifact_count(row)
            if actual == item_count:
                self.mark_success(str(row["task_key"]))
                repaired += 1
            elif actual != 0:
                raise RuntimeError(
                    "contact country work-unit result artifact is partial: "
                    f"expected={item_count} actual={actual}"
                )
        return repaired

    def resume_state(self) -> tuple[str | None, dict[str, Any] | None, int, int]:
        repaired = self.reconcile_committed_units()
        rows = self.rows()
        cursor: str | None = None
        pending: dict[str, Any] | None = None
        expected_index = 1
        for position, row in enumerate(rows):
            task_index = int(row.get("task_index") or 0)
            if task_index != expected_index:
                raise RuntimeError("contact country work-unit ledger has a task-index gap")
            expected_index += 1
            status = str(row.get("status") or "")
            if pending is None and status == "SUCCESS":
                cursor = str(row.get("range_upper") or "") or cursor
                continue
            if pending is None:
                pending = row
                later_success = any(
                    str(item.get("status") or "") == "SUCCESS" for item in rows[position + 1 :]
                )
                if later_success:
                    raise RuntimeError(
                        "contact country work-unit ledger has SUCCESS after incomplete work"
                    )
        next_index = int(pending["task_index"]) if pending else len(rows) + 1
        return cursor, pending, next_index, repaired

    def _read_task(self, job_id: str, task_key: str) -> dict[str, Any] | None:
        self._assert_job_id(job_id)
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, operation_hash
                    FROM contact.country_inference_work_unit
                    WHERE run_id = %s::uuid
                      AND checkpoint_version = %s
                      AND task_key = %s
                    """,
                    (self.run_id, CHECKPOINT_VERSION, task_key),
                )
                row = cur.fetchone()
        return dict(row) if row else None

    def _upsert_running(self, spec: WorkUnitSpec) -> None:
        self._assert_job_id(spec.job_id)
        item_count = self._pending_item_counts.get(spec.task_key)
        if item_count is None:
            raise RuntimeError("contact country work-unit item count was not supplied")
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO contact.country_inference_work_unit (
                        run_id, checkpoint_version, task_key, task_group,
                        task_index, task_total, partition_kind, range_lower, range_upper,
                        operation_hash, item_count, status, attempts, started_at,
                        completed_at, last_error, updated_at
                    ) VALUES (
                        %s::uuid, %s, %s, %s, %s, %s, %s,
                        %s::uuid, %s::uuid, %s, %s,
                        'RUNNING', 1, now(), NULL, '', now()
                    )
                    ON CONFLICT (run_id, checkpoint_version, task_key)
                    DO UPDATE SET
                        task_group = EXCLUDED.task_group,
                        task_index = EXCLUDED.task_index,
                        task_total = EXCLUDED.task_total,
                        partition_kind = EXCLUDED.partition_kind,
                        range_lower = EXCLUDED.range_lower,
                        range_upper = EXCLUDED.range_upper,
                        operation_hash = EXCLUDED.operation_hash,
                        item_count = EXCLUDED.item_count,
                        status = 'RUNNING',
                        attempts = contact.country_inference_work_unit.attempts + 1,
                        started_at = now(),
                        completed_at = NULL,
                        last_error = '',
                        updated_at = now()
                    """,
                    (
                        self.run_id,
                        CHECKPOINT_VERSION,
                        spec.task_key,
                        spec.task_group,
                        int(spec.task_index),
                        int(spec.task_total),
                        spec.partition_kind,
                        spec.partition_lower,
                        spec.partition_upper,
                        spec.operation_hash,
                        int(item_count),
                    ),
                )
            conn.commit()

    def _set_success(self, job_id: str, task_key: str) -> None:
        self._assert_job_id(job_id)
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE contact.country_inference_work_unit
                    SET status = 'SUCCESS',
                        completed_at = now(),
                        last_error = '',
                        updated_at = now()
                    WHERE run_id = %s::uuid
                      AND checkpoint_version = %s
                      AND task_key = %s
                    """,
                    (self.run_id, CHECKPOINT_VERSION, task_key),
                )
            conn.commit()

    def _set_failed(self, job_id: str, task_key: str, error: str) -> None:
        self._assert_job_id(job_id)
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE contact.country_inference_work_unit
                    SET status = 'FAILED',
                        completed_at = NULL,
                        last_error = %s,
                        updated_at = now()
                    WHERE run_id = %s::uuid
                      AND checkpoint_version = %s
                      AND task_key = %s
                    """,
                    (str(error)[-8000:], self.run_id, CHECKPOINT_VERSION, task_key),
                )
            conn.commit()

    def _summarize(self, job_id: str) -> dict[str, int]:
        self._assert_job_id(job_id)
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, count(*) AS row_count
                    FROM contact.country_inference_work_unit
                    WHERE run_id = %s::uuid AND checkpoint_version = %s
                    GROUP BY status
                    """,
                    (self.run_id, CHECKPOINT_VERSION),
                )
                rows = cur.fetchall()
        return {str(row["status"]): int(row["row_count"] or 0) for row in rows}


def validate_resume_batch(batch: list[dict[str, Any]], pending: dict[str, Any]) -> None:
    expected_count = int(pending.get("item_count") or 0)
    expected_lower = str(pending.get("range_lower") or "")
    expected_upper = str(pending.get("range_upper") or "")
    entity_ids = [str(row.get("entity_id") or "") for row in batch]
    if (
        len(entity_ids) != expected_count
        or not entity_ids
        or entity_ids[0] != expected_lower
        or entity_ids[-1] != expected_upper
    ):
        raise RuntimeError(
            "contact country resume range drifted from durable work unit; "
            "refusing to infer a different batch under the same task identity"
        )


def _durable_counters(run_id: str) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    country_counts: Counter[str] = Counter()
    evidence_counts: Counter[str] = Counter()
    applied = 0
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, count(*) AS row_count
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid
                GROUP BY status
                """,
                (run_id,),
            )
            for row in cur.fetchall():
                status_counts[str(row["status"])] = int(row["row_count"] or 0)

            cur.execute(
                """
                SELECT country_code, count(*) AS row_count
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid AND country_code IS NOT NULL
                GROUP BY country_code
                """,
                (run_id,),
            )
            for row in cur.fetchall():
                country_counts[str(row["country_code"])] = int(row["row_count"] or 0)

            cur.execute(
                """
                SELECT evidence_item->>'kind' AS evidence_kind, count(*) AS row_count
                FROM contact.entity_country_inference AS ci
                CROSS JOIN LATERAL jsonb_array_elements(ci.evidence) AS evidence_item
                WHERE ci.last_run_id = %s::uuid
                  AND evidence_item ? 'kind'
                GROUP BY evidence_item->>'kind'
                """,
                (run_id,),
            )
            for row in cur.fetchall():
                evidence_counts[str(row["evidence_kind"])] = int(row["row_count"] or 0)

            cur.execute(
                """
                SELECT count(*) AS row_count
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid AND applied_at IS NOT NULL
                """,
                (run_id,),
            )
            applied = int(cur.fetchone()["row_count"] or 0)

    return {
        "evaluated": sum(status_counts.values()),
        "accepted": status_counts["ACCEPTED"],
        "conflict": status_counts["CONFLICT"],
        "insufficient": status_counts["INSUFFICIENT"],
        "applied": applied,
        "country_counts": dict(country_counts.most_common()),
        "evidence_kind_counts": dict(evidence_counts.most_common()),
    }


def _progress_metrics(
    *,
    run_id: str,
    apply: bool,
    unknown_before: int,
    counters: dict[str, Any],
    store: CountryInferenceWorkStore,
    models: engine.ReferenceModels,
    elapsed_seconds: float,
    max_entities: int | None,
    repaired_units: int,
) -> dict[str, Any]:
    work_units = store.summary()
    return {
        "event": "CONTACT_COUNTRY_INFERENCE_PROGRESS",
        "run_id": run_id,
        "status": "RUNNING",
        "apply": apply,
        "max_entities": max_entities,
        "unknown_before": unknown_before,
        **counters,
        "batches": work_units["SUCCESS"],
        "elapsed_seconds": round(elapsed_seconds, 2),
        "reference_city_keys": len(models.city_country),
        "reference_domain_keys": len(models.domain_country),
        "work_engine": {
            "owner_scope": WORK_OWNER_SCOPE,
            "checkpoint_version": CHECKPOINT_VERSION,
            "partition_kind": PARTITION_KIND,
            "work_units": work_units,
            "reconciled_committed_units": repaired_units,
        },
    }


def _validate_new_run_args(
    *,
    min_confidence: float,
    min_margin: float,
    batch_size: int,
    max_entities: int | None,
) -> None:
    if not 0.5 <= min_confidence <= 0.999:
        raise ValueError("min_confidence must be between 0.5 and 0.999")
    if not 0.0 <= min_margin <= 0.5:
        raise ValueError("min_margin must be between 0 and 0.5")
    if batch_size < 10 or batch_size > 5000:
        raise ValueError("batch_size must be between 10 and 5000")
    if max_entities is not None and max_entities < 1:
        raise ValueError("max_entities must be positive")


def run_country_inference_resumable(
    *,
    apply: bool = False,
    min_confidence: float = engine.DEFAULT_MIN_CONFIDENCE,
    min_margin: float = engine.DEFAULT_MIN_MARGIN,
    batch_size: int = engine.DEFAULT_BATCH_SIZE,
    max_entities: int | None = None,
    resume_run_id: str | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run Contact Country Inference with durable entity-range work units.

    New work refuses to start while another Work Engine-backed inference run is
    unfinished. ``resume_run_id`` reuses the original run configuration and only
    replays the first incomplete range. SUCCESS ranges are never re-evaluated.
    """
    ensure_country_inference_work_schema()
    resumed = resume_run_id is not None

    if resumed:
        row = _read_run(str(resume_run_id))
        if row is None:
            raise ValueError(f"country inference run not found: {resume_run_id}")
        run_id = str(row["run_id"])
        if str(row.get("rule_version") or "") != engine.COUNTRY_INFERENCE_VERSION:
            raise RuntimeError("country inference run rule version is not resumable")
        if str(row.get("status") or "") == "SUCCESS":
            metrics = _run_metrics(row)
            return metrics or {"run_id": run_id, "status": "SUCCESS"}
        if str(row.get("status") or "") == "BUSY":
            raise RuntimeError("BUSY country inference runs are not resumable")
        apply = bool(row.get("apply_mode"))
        min_confidence = float(row.get("min_confidence"))
        min_margin = float(row.get("min_margin"))
        batch_size = int(row.get("batch_size"))
        max_entities = _stored_max_entities(row)
    else:
        _validate_new_run_args(
            min_confidence=min_confidence,
            min_margin=min_margin,
            batch_size=batch_size,
            max_entities=max_entities,
        )
        unfinished = _unfinished_work_run()
        if unfinished:
            raise RuntimeError(
                "unfinished durable country inference run exists; resume with "
                f"--resume-run {unfinished}"
            )
        run_id = _create_run(
            apply=apply,
            min_confidence=min_confidence,
            min_margin=min_margin,
            batch_size=batch_size,
            max_entities=max_entities,
        )

    op_hash = operation_hash(
        apply=apply,
        min_confidence=min_confidence,
        min_margin=min_margin,
        batch_size=batch_size,
        max_entities=max_entities,
    )
    store = CountryInferenceWorkStore(run_id, operation_hash_value=op_hash)

    with postgres_conn() as lock_conn:
        with lock_conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired",
                (engine.COUNTRY_INFERENCE_LOCK,),
            )
            acquired = bool(cur.fetchone()["acquired"])
            lock_conn.commit()
        if not acquired:
            busy = {"run_id": run_id, "status": "BUSY", "apply": apply, "resumed": resumed}
            if not resumed:
                engine._update_run(run_id, status="BUSY", metrics=busy)
            return busy

        started = time.monotonic()
        current_task_key: str | None = None
        repaired_units = 0
        try:
            if resumed:
                _set_run_running(run_id)
                cursor, pending, next_index, repaired_units = store.resume_state()
            else:
                cursor, pending, next_index = None, None, 1

            current_metrics = _run_metrics(_read_run(run_id) or {})
            unknown_before_raw = current_metrics.get("unknown_before")
            if unknown_before_raw is None:
                with lock_conn.cursor() as cur:
                    unknown_before = engine._unknown_contact_count(cur)
            else:
                unknown_before = int(unknown_before_raw)

            models = engine.build_reference_models()
            counters = _durable_counters(run_id)
            initial_progress = _progress_metrics(
                run_id=run_id,
                apply=apply,
                unknown_before=unknown_before,
                counters=counters,
                store=store,
                models=models,
                elapsed_seconds=time.monotonic() - started,
                max_entities=max_entities,
                repaired_units=repaired_units,
            )
            engine._update_run(run_id, metrics=initial_progress)
            if emit is not None and resumed:
                emit(initial_progress)

            while True:
                if pending is not None:
                    take = int(pending["item_count"])
                    batch = engine._candidate_batch(cursor, take)
                    validate_resume_batch(batch, pending)
                    task_key = str(pending["task_key"])
                    task_index = int(pending["task_index"])
                else:
                    evaluated = int(counters["evaluated"])
                    remaining_limit = (
                        None if max_entities is None else max_entities - evaluated
                    )
                    if remaining_limit is not None and remaining_limit <= 0:
                        break
                    take = (
                        batch_size
                        if remaining_limit is None
                        else min(batch_size, remaining_limit)
                    )
                    batch = engine._candidate_batch(cursor, take)
                    if not batch:
                        break
                    entity_ids = [str(row["entity_id"]) for row in batch]
                    task_key = store.task_key(lower=entity_ids[0], upper=entity_ids[-1])
                    task_index = next_index

                entity_ids = [str(row["entity_id"]) for row in batch]
                lower = entity_ids[0]
                upper = entity_ids[-1]
                current_task_key = task_key
                store.mark_running(
                    task_key=task_key,
                    task_index=task_index,
                    lower=lower,
                    upper=upper,
                    item_count=len(entity_ids),
                )

                try:
                    context = engine._load_context(entity_ids)
                    batch_results: list[tuple[str, engine.Inference]] = []
                    for entity in batch:
                        entity_id = str(entity["entity_id"])
                        inference = engine.infer_from_evidence(
                            engine._entity_evidence(entity, context[entity_id], models),
                            min_confidence=min_confidence,
                            min_margin=min_margin,
                        )
                        batch_results.append((entity_id, inference))

                    with postgres_conn() as batch_conn:
                        with batch_conn.cursor() as cur:
                            applied_set: set[str] = set()
                            if apply:
                                for entity_id, inference in batch_results:
                                    if (
                                        inference.status != "ACCEPTED"
                                        or not inference.country_code
                                    ):
                                        continue
                                    cur.execute(
                                        """
                                        SELECT 1
                                        FROM entity.entity
                                        WHERE entity_id = %s AND country_code IS NULL
                                        """,
                                        (entity_id,),
                                    )
                                    if cur.fetchone():
                                        applied_set.add(entity_id)
                            for entity_id, inference in batch_results:
                                engine._upsert_inference(
                                    cur,
                                    run_id,
                                    entity_id,
                                    inference,
                                    entity_id in applied_set,
                                )
                        batch_conn.commit()
                except Exception as exc:
                    store.mark_failed(task_key, f"{type(exc).__name__}: {exc}")
                    raise

                try:
                    store.mark_success(task_key)
                except Exception as exc:
                    try:
                        store.mark_failed(
                            task_key,
                            "result transaction committed; SUCCESS transition failed: "
                            f"{type(exc).__name__}: {exc}",
                        )
                    finally:
                        raise

                current_task_key = None
                cursor = upper
                pending = None
                next_index = task_index + 1
                counters = _durable_counters(run_id)
                progress = _progress_metrics(
                    run_id=run_id,
                    apply=apply,
                    unknown_before=unknown_before,
                    counters=counters,
                    store=store,
                    models=models,
                    elapsed_seconds=time.monotonic() - started,
                    max_entities=max_entities,
                    repaired_units=repaired_units,
                )
                engine._update_run(run_id, metrics=progress)
                if emit is not None:
                    emit(progress)

            work_units = store.assert_complete()
            with postgres_conn() as final_conn:
                with final_conn.cursor() as cur:
                    unknown_after = engine._unknown_contact_count(cur)
            counters = _durable_counters(run_id)
            metrics = {
                "run_id": run_id,
                "status": "SUCCESS",
                "rule_version": engine.COUNTRY_INFERENCE_VERSION,
                "apply": apply,
                "min_confidence": min_confidence,
                "min_margin": min_margin,
                "batch_size": batch_size,
                "max_entities": max_entities,
                "unknown_before": unknown_before,
                "unknown_after": unknown_after,
                **counters,
                "batches": work_units["SUCCESS"],
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "reference_city_keys": len(models.city_country),
                "reference_domain_keys": len(models.domain_country),
                "semantics": "INFERRED_CONTACT_GEO_OVERLAY_NOT_OFFICIAL_TRADEMARK_FACT",
                "source_country_fields_mutated": False,
                "work_engine": {
                    "owner_scope": WORK_OWNER_SCOPE,
                    "checkpoint_version": CHECKPOINT_VERSION,
                    "partition_kind": PARTITION_KIND,
                    "work_units": work_units,
                    "reconciled_committed_units": repaired_units,
                    "resumed": resumed,
                },
            }
            engine._update_run(run_id, status="SUCCESS", metrics=metrics)
            if apply and int(counters["applied"]):
                try:
                    from app.contact_ingest.directory_cached import (
                        invalidate_contact_view_cache,
                    )

                    invalidate_contact_view_cache()
                except Exception:
                    pass
            return metrics
        except Exception as exc:
            if current_task_key:
                try:
                    store.mark_failed(current_task_key, f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass
            try:
                counters = _durable_counters(run_id)
            except Exception:
                counters = {"evaluated": 0, "applied": 0}
            try:
                work_units = store.summary()
            except Exception:
                work_units = {}
            failure = {
                "run_id": run_id,
                "status": "FAILED",
                "apply": apply,
                "max_entities": max_entities,
                "evaluated": int(counters.get("evaluated") or 0),
                "applied": int(counters.get("applied") or 0),
                "error": f"{type(exc).__name__}: {exc}",
                "work_engine": {
                    "owner_scope": WORK_OWNER_SCOPE,
                    "checkpoint_version": CHECKPOINT_VERSION,
                    "work_units": work_units,
                    "resumed": resumed,
                },
            }
            engine._update_run(
                run_id,
                status="FAILED",
                metrics=failure,
                error=failure["error"],
            )
            raise
        finally:
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s))",
                    (engine.COUNTRY_INFERENCE_LOCK,),
                )
            lock_conn.commit()
