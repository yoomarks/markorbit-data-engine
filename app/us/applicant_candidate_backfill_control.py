from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable

from app.db import postgres_conn
from app.us.applicant_candidate_backfill import (
    ApplicantIndexBackfillCursor,
    backfill_us_applicant_candidate_index,
)
from app.us.applicant_candidate_completeness import verify_us_applicant_candidate_index
from app.us.applicant_name_lookup_backfill import (
    ApplicantNameLookupBackfillCursor,
    backfill_us_applicant_name_lookup,
)
from app.us.applicant_name_lookup_completeness import verify_us_applicant_name_lookup
from app.us.target_bulk_tasks import (
    STATUS_NEEDS_OPERATOR,
    STATUS_PREPARE_QUEUED,
    STATUS_RUN_QUEUED,
    STATUS_RUNNING,
    TARGET_BULK_EXECUTION_LANE,
    TARGET_BULK_SOURCE_COUNT,
    TARGET_BULK_TASK_KIND,
)

BACKFILL_JOB_TYPE = "US_APPLICANT_CANDIDATE_BACKFILL_V1"
BACKFILL_TRIGGER_TYPE = "OPERATOR"
_BACKFILL_LOCK = "markorbit:us:applicant-candidate-backfill"
_ACTIVE_BULK_STATUSES = {
    STATUS_PREPARE_QUEUED,
    STATUS_NEEDS_OPERATOR,
    STATUS_RUN_QUEUED,
    STATUS_RUNNING,
}


@dataclass(frozen=True, slots=True)
class USApplicantServingEpoch:
    bulk_run_id: str
    plan_sha256: str
    checkpoint_sequence: int
    final_audit_version: str

    @property
    def token(self) -> str:
        payload = json.dumps(
            {
                "bulk_run_id": self.bulk_run_id,
                "plan_sha256": self.plan_sha256,
                "checkpoint_sequence": self.checkpoint_sequence,
                "final_audit_version": self.final_audit_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "bulk_run_id": self.bulk_run_id,
            "plan_sha256": self.plan_sha256,
            "checkpoint_sequence": self.checkpoint_sequence,
            "final_audit_version": self.final_audit_version,
            "token": self.token,
        }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def current_us_applicant_serving_epoch(
    *,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> USApplicantServingEpoch:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, status, payload, metrics
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                  AND payload->>'domain' = 'US_APPLICATION'
                ORDER BY started_at DESC, run_id DESC
                """,
                (TARGET_BULK_TASK_KIND, TARGET_BULK_EXECUTION_LANE),
            )
            rows = [dict(row) for row in cur.fetchall()]

    active = [row for row in rows if str(row.get("status")) in _ACTIVE_BULK_STATUSES]
    if active:
        raise RuntimeError("US Applicant serving epoch is not quiescent: bulk publication active")

    for row in rows:
        if str(row.get("status")) != "SUCCESS":
            continue
        payload = dict(row.get("payload") or {})
        metrics = dict(row.get("metrics") or {})
        checkpoint = int(metrics.get("last_safe_checkpoint_sequence") or 0)
        if checkpoint != TARGET_BULK_SOURCE_COUNT:
            continue
        if not bool(metrics.get("full_accepted_source_corpus_on_target")):
            continue
        if str(metrics.get("phase") or "") != "COMPLETE":
            continue
        plan_sha256 = str(payload.get("approved_plan_sha256") or metrics.get("plan_sha256") or "")
        if len(plan_sha256) != 64:
            raise RuntimeError("completed US bulk epoch is missing exact plan SHA-256")
        return USApplicantServingEpoch(
            bulk_run_id=str(row["run_id"]),
            plan_sha256=plan_sha256.lower(),
            checkpoint_sequence=checkpoint,
            final_audit_version=str(metrics.get("final_audit_version") or ""),
        )

    raise RuntimeError("no durable complete US Application bulk serving epoch is available")


def start_backfill_run(
    *,
    epoch: USApplicantServingEpoch,
    implementation_sha: str,
    production_mutation_authorized: bool = False,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> str:
    implementation_sha = implementation_sha.strip().lower()
    if len(implementation_sha) != 40:
        raise ValueError("implementation_sha must be a 40-character git SHA")
    if production_mutation_authorized is not True:
        raise PermissionError("explicit production mutation authorization is required for backfill")
    payload = {
        "task_kind": BACKFILL_JOB_TYPE,
        "source_epoch": epoch.to_dict(),
        "implementation_sha": implementation_sha,
        "production_mutation_authorized": True,
    }
    metrics = ApplicantIndexBackfillCursor().to_dict()
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_BACKFILL_LOCK,))
            cur.execute(
                """
                SELECT count(*) AS n
                FROM control.job_run
                WHERE job_type = %s AND status = 'RUNNING'
                """,
                (BACKFILL_JOB_TYPE,),
            )
            if int((cur.fetchone() or {}).get("n", 0) or 0):
                raise RuntimeError("US Applicant candidate backfill is already RUNNING")
            cur.execute(
                """
                INSERT INTO control.job_run (
                    job_type, trigger_type, status, payload, metrics
                )
                VALUES (%s, %s, 'RUNNING', %s::jsonb, %s::jsonb)
                RETURNING run_id
                """,
                (BACKFILL_JOB_TYPE, BACKFILL_TRIGGER_TYPE, _json(payload), _json(metrics)),
            )
            run_id = str(cur.fetchone()["run_id"])
        conn.commit()
    return run_id


def load_backfill_run(
    run_id: str,
    *,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> dict[str, Any]:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, status, payload, metrics, error_message
                FROM control.job_run
                WHERE run_id = %s AND job_type = %s
                """,
                (run_id, BACKFILL_JOB_TYPE),
            )
            row = cur.fetchone()
    if not row:
        raise ValueError("US Applicant candidate backfill run was not found")
    return dict(row)


def checkpoint_backfill_run(
    run_id: str,
    cursor: ApplicantIndexBackfillCursor,
    *,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> None:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.job_run
                SET metrics = metrics || %s::jsonb,
                    error_message = NULL
                WHERE run_id = %s AND job_type = %s AND status = 'RUNNING'
                RETURNING run_id
                """,
                (_json(cursor.to_dict()), run_id, BACKFILL_JOB_TYPE),
            )
            if cur.fetchone() is None:
                raise RuntimeError("backfill checkpoint rejected: run is not RUNNING")
        conn.commit()


def checkpoint_name_lookup_run(
    run_id: str,
    cursor: ApplicantNameLookupBackfillCursor,
    *,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> None:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.job_run
                SET metrics = metrics || jsonb_build_object(
                        'name_lookup_cursor', %s::jsonb,
                        'phase', 'NAME_LOOKUP_BACKFILL'
                    ),
                    error_message = NULL
                WHERE run_id = %s AND job_type = %s AND status = 'RUNNING'
                RETURNING run_id
                """,
                (_json(cursor.to_dict()), run_id, BACKFILL_JOB_TYPE),
            )
            if cur.fetchone() is None:
                raise RuntimeError("name lookup checkpoint rejected: run is not RUNNING")
        conn.commit()


def request_backfill_stop(
    run_id: str,
    *,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> None:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.job_run
                SET payload = payload || jsonb_build_object('stop_requested', true)
                WHERE run_id = %s AND job_type = %s AND status = 'RUNNING'
                RETURNING run_id
                """,
                (run_id, BACKFILL_JOB_TYPE),
            )
            if cur.fetchone() is None:
                raise RuntimeError("backfill stop rejected: run is not RUNNING")
        conn.commit()


def backfill_stop_requested(
    run_id: str,
    *,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> bool:
    run = load_backfill_run(run_id, connection_factory=connection_factory)
    return bool(dict(run.get("payload") or {}).get("stop_requested"))


def _finish_backfill_run(
    run_id: str,
    *,
    status: str,
    metrics: dict[str, Any],
    error_message: str | None,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> None:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.job_run
                SET status = %s,
                    finished_at = now(),
                    metrics = metrics || %s::jsonb,
                    error_message = %s
                WHERE run_id = %s AND job_type = %s
                RETURNING run_id
                """,
                (status, _json(metrics), error_message, run_id, BACKFILL_JOB_TYPE),
            )
            if cur.fetchone() is None:
                raise RuntimeError("backfill finalization rejected: run was not found")
        conn.commit()


def _cursor_from_run(run: dict[str, Any]) -> ApplicantIndexBackfillCursor:
    metrics = dict(run.get("metrics") or {})
    return ApplicantIndexBackfillCursor(
        after_serial=str(metrics.get("after_serial") or ""),
        after_owner_key=str(metrics.get("after_owner_key") or ""),
        emitted=int(metrics.get("emitted") or 0),
    )


def _name_lookup_cursor_from_run(
    run: dict[str, Any],
) -> ApplicantNameLookupBackfillCursor:
    metrics = dict(run.get("metrics") or {})
    cursor = dict(metrics.get("name_lookup_cursor") or {})
    return ApplicantNameLookupBackfillCursor(
        after_candidate_key=str(cursor.get("after_candidate_key") or ""),
        after_serial=str(cursor.get("after_serial") or ""),
        after_owner_key=str(cursor.get("after_owner_key") or ""),
        emitted=int(cursor.get("emitted") or 0),
    )


def _expected_epoch_from_run(run: dict[str, Any]) -> str:
    payload = dict(run.get("payload") or {})
    source_epoch = dict(payload.get("source_epoch") or {})
    token = str(source_epoch.get("token") or "")
    if len(token) != 64:
        raise RuntimeError("backfill run is missing a frozen source epoch token")
    return token


def resume_backfill_run(
    run_id: str,
    *,
    current_epoch: USApplicantServingEpoch,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> ApplicantIndexBackfillCursor:
    run = load_backfill_run(run_id, connection_factory=connection_factory)
    expected_epoch = _expected_epoch_from_run(run)
    if expected_epoch != current_epoch.token:
        raise RuntimeError("backfill source epoch changed; rebuild is required")
    status = str(run.get("status") or "")
    if status not in {"RUNNING", "INTERRUPTED"}:
        raise RuntimeError(f"backfill run is not resumable from status={status}")

    if status == "INTERRUPTED":
        with connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE control.job_run
                    SET status = 'RUNNING', finished_at = NULL, error_message = NULL
                    WHERE run_id = %s AND job_type = %s AND status = 'INTERRUPTED'
                    RETURNING run_id
                    """,
                    (run_id, BACKFILL_JOB_TYPE),
                )
                if cur.fetchone() is None:
                    raise RuntimeError("backfill resume compare-and-set failed")
            conn.commit()
    return _cursor_from_run(run)


def complete_backfill_run(
    run_id: str,
    *,
    current_epoch: USApplicantServingEpoch,
    completeness: dict[str, Any],
    connection_factory: Callable[..., Any] = postgres_conn,
) -> None:
    run = load_backfill_run(run_id, connection_factory=connection_factory)
    if _expected_epoch_from_run(run) != current_epoch.token:
        raise RuntimeError("cannot complete backfill against a changed source epoch")
    if str(run.get("status") or "") != "RUNNING":
        raise RuntimeError("cannot complete a backfill that is not RUNNING")
    if completeness.get("complete") is not True:
        raise RuntimeError("US Applicant candidate completeness receipt is not accepted")
    _finish_backfill_run(
        run_id,
        status="SUCCESS",
        metrics={"completeness": completeness, "source_epoch_token": current_epoch.token},
        error_message=None,
        connection_factory=connection_factory,
    )


def interrupt_backfill_run(
    run_id: str,
    *,
    error_message: str,
    resumable: bool,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> None:
    _finish_backfill_run(
        run_id,
        status="INTERRUPTED" if resumable else "FAILED",
        metrics={"resumable": bool(resumable), "requires_rebuild": not resumable},
        error_message=error_message,
        connection_factory=connection_factory,
    )


def applicant_index_ready_for_epoch(
    epoch: USApplicantServingEpoch,
    *,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> bool:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload, metrics
                FROM control.job_run
                WHERE job_type = %s AND status = 'SUCCESS'
                ORDER BY finished_at DESC NULLS LAST, started_at DESC
                LIMIT 1
                """,
                (BACKFILL_JOB_TYPE,),
            )
            row = cur.fetchone()
    if not row:
        return False
    payload = dict(row.get("payload") or {})
    metrics = dict(row.get("metrics") or {})
    source_epoch = dict(payload.get("source_epoch") or {})
    completeness = dict(metrics.get("completeness") or {})
    return (
        str(source_epoch.get("token") or "") == epoch.token
        and completeness.get("complete") is True
        and str(metrics.get("source_epoch_token") or "") == epoch.token
    )


def applicant_name_lookup_ready_for_epoch(
    epoch: USApplicantServingEpoch,
    *,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> bool:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload, metrics
                FROM control.job_run
                WHERE job_type = %s AND status = 'SUCCESS'
                ORDER BY finished_at DESC NULLS LAST, started_at DESC
                LIMIT 1
                """,
                (BACKFILL_JOB_TYPE,),
            )
            row = cur.fetchone()
    if not row:
        return False
    payload = dict(row.get("payload") or {})
    metrics = dict(row.get("metrics") or {})
    source_epoch = dict(payload.get("source_epoch") or {})
    completeness = dict(metrics.get("completeness") or {})
    lookup = dict(completeness.get("name_lookup") or {})
    return (
        str(source_epoch.get("token") or "") == epoch.token
        and str(metrics.get("source_epoch_token") or "") == epoch.token
        and completeness.get("complete") is True
        and completeness.get("name_lookup_complete") is True
        and lookup.get("complete") is True
    )


def execute_backfill_run(
    run_id: str,
    *,
    client: Any,
    serving_epoch_getter: Callable[
        [], USApplicantServingEpoch
    ] = current_us_applicant_serving_epoch,
    connection_factory: Callable[..., Any] = postgres_conn,
    batch_size: int = 5_000,
) -> dict[str, Any]:
    epoch = serving_epoch_getter()
    cursor = resume_backfill_run(
        run_id,
        current_epoch=epoch,
        connection_factory=connection_factory,
    )
    try:
        final_cursor = backfill_us_applicant_candidate_index(
            client=client,
            batch_size=batch_size,
            cursor=cursor,
            expected_epoch=epoch.token,
            serving_epoch_getter=lambda: serving_epoch_getter().token,
            checkpoint=lambda state: checkpoint_backfill_run(
                run_id,
                state,
                connection_factory=connection_factory,
            ),
            stop_requested=lambda: backfill_stop_requested(
                run_id, connection_factory=connection_factory
            ),
        )
        run = load_backfill_run(run_id, connection_factory=connection_factory)
        name_cursor = _name_lookup_cursor_from_run(run)
        final_name_cursor = backfill_us_applicant_name_lookup(
            client=client,
            batch_size=batch_size,
            cursor=name_cursor,
            expected_epoch=epoch.token,
            serving_epoch_getter=lambda: serving_epoch_getter().token,
            checkpoint=lambda state: checkpoint_name_lookup_run(
                run_id,
                state,
                connection_factory=connection_factory,
            ),
            stop_requested=lambda: backfill_stop_requested(
                run_id, connection_factory=connection_factory
            ),
        )
        current_epoch = serving_epoch_getter()
        if current_epoch.token != epoch.token:
            raise RuntimeError(
                "US Applicant serving epoch changed before completeness verification"
            )
        candidate_completeness = verify_us_applicant_candidate_index(client)
        name_lookup_completeness = verify_us_applicant_name_lookup(client)
        completeness = {
            **candidate_completeness,
            "complete": candidate_completeness.get("complete") is True
            and name_lookup_completeness.get("complete") is True,
            "candidate_index_complete": candidate_completeness.get("complete") is True,
            "name_lookup_complete": name_lookup_completeness.get("complete") is True,
            "name_lookup": name_lookup_completeness,
        }
        complete_backfill_run(
            run_id,
            current_epoch=current_epoch,
            completeness=completeness,
            connection_factory=connection_factory,
        )
        return {
            "run_id": run_id,
            "cursor": final_cursor.to_dict(),
            "name_lookup_cursor": final_name_cursor.to_dict(),
            "source_epoch": current_epoch.to_dict(),
            "completeness": completeness,
        }
    except Exception as exc:
        try:
            current_token = serving_epoch_getter().token
        except Exception:
            current_token = ""
        resumable = current_token == epoch.token
        interrupt_backfill_run(
            run_id,
            error_message=f"{type(exc).__name__}: {exc}",
            resumable=resumable,
            connection_factory=connection_factory,
        )
        raise
