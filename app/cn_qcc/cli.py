from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from app.cn_qcc.exporter import export_batch
from app.cn_qcc.incoming import ingest_result
from app.cn_qcc.operator import acquisition_state, run_cycle
from app.cn_qcc.planner import create_batch, plan_as_dict
from app.config import get_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CN Qichacha applicant enrichment control")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="create or return the current bounded QCC batch")
    plan.add_argument("--capacity", type=int, required=True)
    plan.add_argument("--refresh-days", type=int, default=180)

    export = sub.add_parser("export", help="export one company-per-row QCC task CSV")
    export.add_argument("--batch-id", required=True)
    export.add_argument("--out", type=Path, required=True)

    ingest = sub.add_parser("ingest", help="ingest the returned QCC enrichment CSV")
    ingest.add_argument("--batch-id", required=True)
    ingest.add_argument("--csv", type=Path, required=True)

    sub.add_parser("state", help="show current QCC acquisition readiness and operator action")
    sub.add_parser("cycle", help="run one idempotent scheduler-friendly QCC acquisition cycle")
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = get_settings()
    if args.command == "plan":
        payload = plan_as_dict(create_batch(capacity=args.capacity, refresh_days=args.refresh_days))
    elif args.command == "export":
        payload = export_batch(args.batch_id, args.out)
    elif args.command == "ingest":
        payload = ingest_result(args.batch_id, args.csv)
    elif args.command == "state":
        payload = asdict(
            acquisition_state(
                enabled=settings.cn_qcc_acquisition_enabled,
                incoming_root=settings.resolved_cn_qcc_incoming_root,
            )
        )
    else:
        payload = run_cycle(
            enabled=settings.cn_qcc_acquisition_enabled,
            capacity=settings.cn_qcc_capacity,
            refresh_days=settings.cn_qcc_refresh_days,
            outgoing_root=settings.resolved_cn_qcc_outgoing_root,
            incoming_root=settings.resolved_cn_qcc_incoming_root,
        )
    print(json.dumps(payload, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
