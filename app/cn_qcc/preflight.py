from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from pathlib import Path

from app.cn_qcc.migrations import ensure_qcc_schema
from app.db import postgres_conn


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    severity: str
    detail: str


@dataclass(frozen=True)
class PreflightReport:
    ready: bool
    checks: tuple[PreflightCheck, ...]
    open_batch_id: str
    open_batch_status: str
    open_batch_age_hours: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "checks": [asdict(check) for check in self.checks],
            "open_batch_id": self.open_batch_id,
            "open_batch_status": self.open_batch_status,
            "open_batch_age_hours": self.open_batch_age_hours,
        }


def _path_check(name: str, path: Path) -> PreflightCheck:
    resolved = path.resolve()
    target = resolved if resolved.exists() else resolved.parent
    if not target.exists():
        return PreflightCheck(name, False, "BLOCKER", f"directory does not exist: {target}")
    if not target.is_dir():
        return PreflightCheck(name, False, "BLOCKER", f"not a directory: {target}")
    writable = os.access(target, os.W_OK)
    return PreflightCheck(
        name,
        writable,
        "INFO" if writable else "BLOCKER",
        f"directory {'writable' if writable else 'not writable'}: {target}",
    )


def production_preflight(
    *,
    capacity: int,
    refresh_days: int,
    cycle_interval_seconds: int,
    stale_batch_hours: int,
    outgoing_root: Path,
    incoming_root: Path,
    now: datetime | None = None,
) -> PreflightReport:
    """Return a read-only production enablement report for periodic QCC acquisition."""
    checks: list[PreflightCheck] = [
        PreflightCheck(
            "capacity",
            capacity > 0,
            "INFO" if capacity > 0 else "BLOCKER",
            f"capacity={capacity}",
        ),
        PreflightCheck(
            "refresh_days",
            refresh_days > 0,
            "INFO" if refresh_days > 0 else "BLOCKER",
            f"refresh_days={refresh_days}",
        ),
        PreflightCheck(
            "cycle_interval_seconds",
            cycle_interval_seconds >= 60,
            "INFO" if cycle_interval_seconds >= 60 else "BLOCKER",
            f"cycle_interval_seconds={cycle_interval_seconds}",
        ),
        PreflightCheck(
            "stale_batch_hours",
            stale_batch_hours > 0,
            "INFO" if stale_batch_hours > 0 else "BLOCKER",
            f"stale_batch_hours={stale_batch_hours}",
        ),
        _path_check("outgoing_root", outgoing_root),
        _path_check("incoming_root", incoming_root),
    ]

    ensure_qcc_schema()
    open_batch_id = ""
    open_batch_status = ""
    open_batch_age_hours: float | None = None
    reference_now = now or datetime.now(timezone.utc)

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT batch_id, batch_key, status, planned_at, task_count, export_path
                FROM acquisition.cn_qcc_batch
                WHERE status IN ('PLANNED', 'EXPORTED', 'RESULT_RECEIVED')
                ORDER BY planned_at DESC
                """
            )
            open_batches = cur.fetchall()
            single_open = len(open_batches) <= 1
            checks.append(
                PreflightCheck(
                    "single_open_batch",
                    single_open,
                    "INFO" if single_open else "BLOCKER",
                    f"open_batches={len(open_batches)}",
                )
            )
            if open_batches:
                batch = open_batches[0]
                open_batch_id = str(batch["batch_id"])
                open_batch_status = str(batch["status"])
                planned_at = batch["planned_at"]
                if planned_at.tzinfo is None:
                    planned_at = planned_at.replace(tzinfo=timezone.utc)
                open_batch_age_hours = max(0.0, (reference_now - planned_at).total_seconds() / 3600.0)
                stale = open_batch_age_hours > stale_batch_hours
                checks.append(
                    PreflightCheck(
                        "open_batch_not_stale",
                        not stale,
                        "INFO" if not stale else "BLOCKER",
                        f"age_hours={open_batch_age_hours:.2f}; limit_hours={stale_batch_hours}",
                    )
                )
                if open_batch_status == "EXPORTED":
                    stored_export_path = str(batch.get("export_path") or "")
                    export_evidence = bool(stored_export_path)
                    checks.append(
                        PreflightCheck(
                            "export_evidence_present",
                            export_evidence,
                            "INFO" if export_evidence else "BLOCKER",
                            f"stored_export_path={'present' if export_evidence else 'missing'}",
                        )
                    )

            cur.execute(
                """
                SELECT count(*) AS n
                FROM acquisition.cn_qcc_task t
                LEFT JOIN acquisition.cn_qcc_batch b ON b.batch_id = t.batch_id
                WHERE b.batch_id IS NULL
                """
            )
            orphan_tasks = int(cur.fetchone()["n"])
            checks.append(
                PreflightCheck(
                    "no_orphan_tasks",
                    orphan_tasks == 0,
                    "INFO" if orphan_tasks == 0 else "BLOCKER",
                    f"orphan_tasks={orphan_tasks}",
                )
            )

    ready = all(check.ok for check in checks if check.severity == "BLOCKER")
    return PreflightReport(
        ready=ready,
        checks=tuple(checks),
        open_batch_id=open_batch_id,
        open_batch_status=open_batch_status,
        open_batch_age_hours=open_batch_age_hours,
    )


__all__ = ["PreflightCheck", "PreflightReport", "production_preflight"]
