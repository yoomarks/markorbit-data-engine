from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable

from app.cn.applicant_name_lookup_backfill import (
    CNApplicantNameLookupBackfillCursor,
    backfill_cn_applicant_name_lookup,
)
from app.cn.applicant_name_lookup_completeness import verify_cn_applicant_name_lookup
from app.cn.research_filing_to_prelim_duration import ServingEpoch, _serving_epoch
from app.db import postgres_conn

BACKFILL_JOB_TYPE = "CN_APPLICANT_NAME_LOOKUP_BACKFILL_V1"
BACKFILL_TRIGGER_TYPE = "OPERATOR"
_BACKFILL_LOCK = "markorbit:cn:applicant-name-lookup-backfill"


@dataclass(frozen=True, slots=True)
class CNApplicantServingEpoch:
    coverage_date: str
    max_success_sequence: int
    success_count: int

    @classmethod
    def from_serving_epoch(cls, epoch: ServingEpoch) -> "CNApplicantServingEpoch":
        return cls(
            coverage_date=epoch.coverage_date.isoformat(),
            max_success_sequence=epoch.max_success_sequence,
            success_count=epoch.success_count,
        )

    @property
    def token(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.to_dict(include_token=False), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    def to_dict(self, *, include_token: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "coverage_date": self.coverage_date,
            "max_success_sequence": self.max_success_sequence,
            "success_count": self.success_count,
        }
        if include_token:
            value["token"] = self.token
        return value


def current_cn_applicant_serving_epoch() -> CNApplicantServingEpoch:
    return CNApplicantServingEpoch.from_serving_epoch(_serving_epoch())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def start_backfill_run(
    *,
    epoch: CNApplicantServingEpoch,
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
        "stop_requested": False,
    }
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_BACKFILL_LOCK,))
            cur.execute(
                "SELECT count(*) AS n FROM control.job_run WHERE job_type = %s AND status = 'RUNNING'",
                (BACKFILL_JOB_TYPE,),
            )
            if int((cur.fetchone() or {}).get("n", 0) or 0):
                raise RuntimeError("CN Applicant name lookup backfill is already RUNNING")
            cur.execute(
                "INSERT INTO control.job_run (job_type, trigger_type, status, payload, metrics) VALUES (%s, %s, 'RUNNING', %s::jsonb, %s::jsonb) RETURNING run_id",
                (
                    BACKFILL_JOB_TYPE,
                    BACKFILL_TRIGGER_TYPE,
                    _json(payload),
                    _json(CNApplicantNameLookupBackfillCursor().to_dict()),
                ),
            )
            run_id = str(cur.fetchone()["run_id"])
        conn.commit()
    return run_id


def load_backfill_run(
    run_id: str, *, connection_factory: Callable[..., Any] = postgres_conn
) -> dict[str, Any]:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT run_id, status, payload, metrics, error_message FROM control.job_run WHERE run_id = %s AND job_type = %s",
                (run_id, BACKFILL_JOB_TYPE),
            )
            row = cur.fetchone()
    if not row:
        raise ValueError("CN Applicant name lookup backfill run was not found")
    return dict(row)


def checkpoint_backfill_run(
    run_id: str,
    cursor: CNApplicantNameLookupBackfillCursor,
    *,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> None:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE control.job_run SET metrics = metrics || %s::jsonb, error_message = NULL WHERE run_id = %s AND job_type = %s AND status = 'RUNNING' RETURNING run_id",
                (_json(cursor.to_dict()), run_id, BACKFILL_JOB_TYPE),
            )
            if cur.fetchone() is None:
                raise RuntimeError("backfill checkpoint rejected: run is not RUNNING")
        conn.commit()


def request_backfill_stop(
    run_id: str, *, connection_factory: Callable[..., Any] = postgres_conn
) -> None:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE control.job_run SET payload = payload || jsonb_build_object('stop_requested', true) WHERE run_id = %s AND job_type = %s AND status = 'RUNNING' RETURNING run_id",
                (run_id, BACKFILL_JOB_TYPE),
            )
            if cur.fetchone() is None:
                raise RuntimeError("backfill stop rejected: run is not RUNNING")
        conn.commit()


def _finish(
    run_id: str,
    status: str,
    metrics: dict[str, Any],
    error: str | None,
    connection_factory: Callable[..., Any],
) -> None:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE control.job_run SET status = %s, finished_at = now(), metrics = metrics || %s::jsonb, error_message = %s WHERE run_id = %s AND job_type = %s RETURNING run_id",
                (status, _json(metrics), error, run_id, BACKFILL_JOB_TYPE),
            )
            if cur.fetchone() is None:
                raise RuntimeError("backfill finalization rejected: run was not found")
        conn.commit()


def _cursor(run: dict[str, Any]) -> CNApplicantNameLookupBackfillCursor:
    metrics = dict(run.get("metrics") or {})
    return CNApplicantNameLookupBackfillCursor(
        str(metrics.get("after_application_number") or ""),
        str(metrics.get("after_role") or ""),
        str(metrics.get("after_relation_key") or ""),
        int(metrics.get("emitted") or 0),
    )


def resume_backfill_run(
    run_id: str,
    *,
    current_epoch: CNApplicantServingEpoch,
    connection_factory: Callable[..., Any] = postgres_conn,
) -> CNApplicantNameLookupBackfillCursor:
    run = load_backfill_run(run_id, connection_factory=connection_factory)
    expected = str(
        dict(dict(run.get("payload") or {}).get("source_epoch") or {}).get("token") or ""
    )
    if expected != current_epoch.token:
        raise RuntimeError("backfill source epoch changed; rebuild is required")
    status = str(run.get("status") or "")
    if status not in {"RUNNING", "INTERRUPTED"}:
        raise RuntimeError(f"backfill run is not resumable from status={status}")
    if status == "INTERRUPTED":
        with connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE control.job_run SET status = 'RUNNING', finished_at = NULL, error_message = NULL, payload = payload || jsonb_build_object('stop_requested', false) WHERE run_id = %s AND job_type = %s AND status = 'INTERRUPTED' RETURNING run_id",
                    (run_id, BACKFILL_JOB_TYPE),
                )
                if cur.fetchone() is None:
                    raise RuntimeError("backfill resume compare-and-set failed")
            conn.commit()
    return _cursor(run)


def lookup_ready_for_epoch(
    epoch: CNApplicantServingEpoch, *, connection_factory: Callable[..., Any] = postgres_conn
) -> bool:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload, metrics FROM control.job_run WHERE job_type = %s AND status = 'SUCCESS' ORDER BY finished_at DESC NULLS LAST, started_at DESC LIMIT 1",
                (BACKFILL_JOB_TYPE,),
            )
            row = cur.fetchone()
    if not row:
        return False
    payload, metrics = dict(row.get("payload") or {}), dict(row.get("metrics") or {})
    completeness = dict(metrics.get("completeness") or {})
    return (
        str(dict(payload.get("source_epoch") or {}).get("token") or "") == epoch.token
        and str(metrics.get("source_epoch_token") or "") == epoch.token
        and completeness.get("complete") is True
    )


def execute_backfill_run(
    run_id: str,
    *,
    client: Any,
    serving_epoch_getter: Callable[
        [], CNApplicantServingEpoch
    ] = current_cn_applicant_serving_epoch,
    connection_factory: Callable[..., Any] = postgres_conn,
    batch_size: int = 5_000,
) -> dict[str, Any]:
    epoch = serving_epoch_getter()
    cursor = resume_backfill_run(run_id, current_epoch=epoch, connection_factory=connection_factory)
    try:
        final = backfill_cn_applicant_name_lookup(
            client=client,
            batch_size=batch_size,
            cursor=cursor,
            expected_epoch=epoch.token,
            serving_epoch_getter=lambda: serving_epoch_getter().token,
            checkpoint=lambda state: checkpoint_backfill_run(
                run_id, state, connection_factory=connection_factory
            ),
            stop_requested=lambda: bool(
                dict(
                    load_backfill_run(run_id, connection_factory=connection_factory).get("payload")
                    or {}
                ).get("stop_requested")
            ),
        )
        current = serving_epoch_getter()
        if current.token != epoch.token:
            raise RuntimeError(
                "CN Applicant serving epoch changed before completeness verification"
            )
        completeness = verify_cn_applicant_name_lookup(client)
        if completeness.get("complete") is not True:
            raise RuntimeError("CN Applicant name lookup completeness receipt is not accepted")
        _finish(
            run_id,
            "SUCCESS",
            {"completeness": completeness, "source_epoch_token": current.token},
            None,
            connection_factory,
        )
        return {
            "run_id": run_id,
            "cursor": final.to_dict(),
            "source_epoch": current.to_dict(),
            "completeness": completeness,
        }
    except Exception as exc:
        try:
            resumable = serving_epoch_getter().token == epoch.token
        except Exception:
            resumable = False
        _finish(
            run_id,
            "INTERRUPTED" if resumable else "FAILED",
            {"resumable": resumable, "requires_rebuild": not resumable},
            f"{type(exc).__name__}: {exc}",
            connection_factory,
        )
        raise
