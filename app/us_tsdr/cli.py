from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.us_tsdr.exporter import export_batch
from app.us_tsdr.incoming import ingest_result_package
from app.us_tsdr.planner import create_weekly_batch, planner_state
from app.us_tsdr.policy import DEFAULT_WEEKLY_CAPACITY
from app.us_tsdr.source_candidates import load_candidate_pool


def _plan(args) -> dict[str, object]:
    state = planner_state()
    watermark = (
        int(state["source_rank_watermark"] or 0),
        str(state["source_serial_watermark"] or ""),
    )
    pool = load_candidate_pool(
        source_watermark=watermark,
        capacity=args.capacity,
        backfill_bucket=args.backfill_bucket,
    )
    result = create_weekly_batch(
        pool.candidates,
        capacity=args.capacity,
        backfill_bucket=pool.backfill_bucket,
        source_watermark_to=pool.source_watermark_to,
    )
    result["candidate_lane_counts"] = pool.lane_counts
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="US TSDR weekly acquisition control")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="plan one bounded weekly TSDR batch")
    plan.add_argument("--capacity", type=int, default=DEFAULT_WEEKLY_CAPACITY)
    plan.add_argument(
        "--backfill-bucket",
        type=int,
        choices=range(52),
        default=datetime.now().isocalendar().week % 52,
    )

    export = sub.add_parser("export", help="export a planned batch for the external collector")
    export.add_argument("batch_key")
    export.add_argument("--outgoing-root", type=Path)

    ingest = sub.add_parser("ingest", help="ingest one collector result package")
    ingest.add_argument("package_dir", type=Path)

    sub.add_parser("state", help="show durable weekly planner state")

    args = parser.parse_args()
    if args.command == "plan":
        result = _plan(args)
    elif args.command == "export":
        result = export_batch(args.batch_key, outgoing_root=args.outgoing_root)
    elif args.command == "ingest":
        result = ingest_result_package(args.package_dir)
    else:
        result = planner_state()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
