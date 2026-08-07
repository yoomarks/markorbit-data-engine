import logging
import time

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
            result = scan_and_ingest_cn(trigger_type="SCHEDULED")
            logger.info("CN cycle completed: %s", result)
        except Exception:
            logger.exception("CN cycle failed")
        time.sleep(max(settings.cn_scan_interval_seconds, 60))


if __name__ == "__main__":
    main()
