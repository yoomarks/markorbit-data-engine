from __future__ import annotations

import json
import sys

from app.jobs import scan_and_ingest_cn


def main() -> int:
    print(
        json.dumps(
            {
                "event": "CN_RUN_START",
                "mode": "DEDICATED_ONE_SHOT",
                "note": "PostgreSQL advisory lock prevents concurrent CN ingestion.",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        result = scan_and_ingest_cn(trigger_type="MANUAL_CLI")
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "CN_RUN_FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        raise

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
    if result.get("ingest", {}).get("busy"):
        return 3
    if result.get("ingest", {}).get("failed"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
