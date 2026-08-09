import logging
import time

from app.cn.guarded_run_once import build_execution_guard
from app.cn.migrations import ensure_m15_schema
from app.config import get_settings
from app.jobs import ensure_raw_directories, scan_and_ingest_cn


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("markorbit.worker")
    ensure_raw_directories()
    ensure_m15_schema()

    logger.info(
        "Worker started; CN scan interval=%s seconds",
        settings.cn_scan_interval_seconds,
    )
    while True:
        try:
            guard = build_execution_guard()
            if not guard.get("allowed"):
                logger.error("CN cycle blocked by M1.6 execution guard: %s", guard)
            elif guard.get("mode") == "CLEAN_RESET_FIRST_RUN":
                # A clean reset must be bootstrapped by the explicit manual
                # run-cn.ps1 path. This keeps the persistent worker from silently
                # beginning a multi-package replay simply because it was started.
                logger.warning(
                    "CN clean replay is ready but requires manual first run; "
                    "persistent worker will not bootstrap it automatically: %s",
                    guard,
                )
            else:
                result = scan_and_ingest_cn(trigger_type="SCHEDULED_GUARDED")
                logger.info("CN cycle completed: %s", result)
        except Exception:
            logger.exception("CN guarded cycle failed")
        time.sleep(max(settings.cn_scan_interval_seconds, 60))


if __name__ == "__main__":
    main()
