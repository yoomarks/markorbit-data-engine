from __future__ import annotations

import json
import logging
import time

from app.cn_qcc.operator import run_cycle
from app.config import get_settings


logger = logging.getLogger("markorbit.cn_qcc.runner")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    interval = max(60, int(settings.cn_qcc_cycle_interval_seconds))

    while True:
        try:
            result = run_cycle(
                enabled=settings.cn_qcc_acquisition_enabled,
                capacity=settings.cn_qcc_capacity,
                refresh_days=settings.cn_qcc_refresh_days,
                outgoing_root=settings.resolved_cn_qcc_outgoing_root,
                incoming_root=settings.resolved_cn_qcc_incoming_root,
            )
            logger.info("CN_QCC_ACQUISITION_CYCLE %s", json.dumps(result, ensure_ascii=False, default=str))
        except Exception:
            logger.exception("CN_QCC_ACQUISITION_CYCLE_FAILED")
        time.sleep(interval)


if __name__ == "__main__":
    main()
