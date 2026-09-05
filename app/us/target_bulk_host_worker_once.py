from __future__ import annotations

import argparse
import logging
import os

from app.us import target_bulk_host_worker_v2 as worker
from app.us.target_bulk_task_control import fail_closed_recover_target_bulk_tasks


ONE_CLAIM_VERSION = "US_APPLICATION_TARGET_BULK_HOST_WORKER_ONE_CLAIM_V1"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-claim Windows host shim for guarded US Application target bulk tasks"
    )
    parser.add_argument("--poll-seconds", type=float, default=worker.POLL_SECONDS)
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("US target bulk host worker must run on Windows")
    if args.poll_seconds < 0.5:
        parser.error("--poll-seconds must be at least 0.5")

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("markorbit.us.target_bulk_host_worker_once")
    recovery = fail_closed_recover_target_bulk_tasks()
    if any(recovery.values()):
        logger.warning("US target bulk host recovery: %s", recovery)

    # No claim is a normal idle poll, not a worker failure. Real exceptions still
    # propagate so the supervisor exits non-zero and Task Scheduler applies the
    # bounded restart policy.
    claimed = worker.run_once()
    logger.info("%s claimed_task=%s", ONE_CLAIM_VERSION, claimed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
