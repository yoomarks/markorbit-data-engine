from __future__ import annotations

import json
import sys

from app.us.jobs import ingest_pending_us


def main() -> int:
    try:
        result = ingest_pending_us(
            trigger_type="MANUAL_US_RETRY",
            include_failed=True,
            limit=1,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "US_RETRY_FAILED",
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
    if result.get("busy"):
        return 3
    if result.get("failed"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
