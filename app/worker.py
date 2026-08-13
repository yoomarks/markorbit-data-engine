import logging
import time

from app.admin_domain_tasks import (
    claim_next_admin_domain_task,
    engine_mutation_guard,
    finish_admin_domain_task,
    recover_interrupted_admin_domain_tasks,
)
from app.cn.guarded_run_once import build_execution_guard
from app.cn.migrations import ensure_m15_schema
from app.config import get_settings
from app.jobs import ensure_raw_directories, scan_and_ingest_cn


_ADMIN_POLL_SECONDS = 2.0


def _run_scheduled_cn(logger) -> None:
    guard = build_execution_guard()
    if not guard.get("allowed"):
        logger.error("CN cycle blocked by M1.6 execution guard: %s", guard)
    elif guard.get("mode") == "CLEAN_RESET_FIRST_RUN":
        # A clean reset must be bootstrapped by the explicit manual run-cn.ps1
        # path. Admin controls and the persistent worker never silently begin it.
        logger.warning(
            "CN clean replay is ready but requires manual first run; "
            "persistent worker will not bootstrap it automatically: %s",
            guard,
        )
    else:
        result = scan_and_ingest_cn(trigger_type="SCHEDULED_GUARDED")
        logger.info("CN cycle completed: %s", result)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("markorbit.worker")
    ensure_raw_directories()
    ensure_m15_schema()
    recovered = recover_interrupted_admin_domain_tasks()
    if recovered:
        logger.warning("Requeued %s interrupted Admin domain task(s)", recovered)

    interval = max(settings.cn_scan_interval_seconds, 60)
    next_cn_at = time.monotonic()
    logger.info(
        "Worker started; CN scan interval=%s seconds; Admin task poll=%s seconds",
        interval,
        _ADMIN_POLL_SECONDS,
    )
    while True:
        try:
            with engine_mutation_guard() as acquired:
                if acquired:
                    task = claim_next_admin_domain_task()
                    if task is not None:
                        logger.info(
                            "Admin domain task started: run_id=%s job_type=%s",
                            task.get("run_id"),
                            task.get("job_type"),
                        )
                        finish_admin_domain_task(task)
                        logger.info("Admin domain task finished: run_id=%s", task.get("run_id"))
                    elif time.monotonic() >= next_cn_at:
                        _run_scheduled_cn(logger)
                        next_cn_at = time.monotonic() + interval
        except Exception:
            logger.exception("Worker cycle failed")
        time.sleep(_ADMIN_POLL_SECONDS)


if __name__ == "__main__":
    main()
