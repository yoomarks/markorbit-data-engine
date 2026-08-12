from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.contact_ingest.planner import build_plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Auto-detect and ingest structured entity/contact files. Dry-run by default."
    )
    parser.add_argument("--input", required=True, help="XLSX/CSV/TSV/JSON/JSONL/ZIP input path")
    parser.add_argument("--source-name", default="", help="Optional source display name")
    parser.add_argument("--apply", action="store_true", help="Apply the detected plan to PostgreSQL")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        plan = build_plan(Path(args.input), source_name=args.source_name)
        payload = {"status": "PLAN_READY", "apply": bool(args.apply), "plan": plan.summary()}
        if args.apply:
            from app.contact_ingest.repository import apply_plan
            payload["result"] = apply_plan(plan)
            payload["status"] = "SUCCESS"
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
