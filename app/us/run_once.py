from __future__ import annotations

import json
import sys

from app.us.jobs import scan_and_ingest_us


def main() -> int:
    print(
        json.dumps(
            {
                "event": "US_RUN_START",
                "mode": "DEDICATED_WORKER_ONE_SHOT",
                "recovery": "PACKAGE_REPLAY",
                "source": "USPTO_TDXF_DAILY_APPLICATIONS",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        result = scan_and_ingest_us(trigger_type="MANUAL_US_WORKER")
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "US_RUN_FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
    if result.get("ingest", {}).get("busy"):
        return 3
    if result.get("ingest", {}).get("failed"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
