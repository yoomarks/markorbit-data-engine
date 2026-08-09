from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from app.config import get_settings
from app.us.deadline_portfolio import scan_deadline_candidate_page


MAX_RESULT_BUFFER = 5000


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Stream bounded U.S. trademark deadline candidates to JSONL. "
            "The output is not a legal-status or final docket conclusion."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--horizon-days", type=int, default=90)
    parser.add_argument("--recent-past-days", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-cases", type=int, default=None)
    args = parser.parse_args()

    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size must be between 1 and 500")
    if args.max_cases is not None and args.max_cases < 1:
        parser.error("--max-cases must be positive")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cursor = ""
    scanned_total = 0
    candidate_total = 0
    page_count = 0
    role_state = None

    with args.output.open("w", encoding="utf-8") as handle:
        while True:
            remaining = (
                args.max_cases - scanned_total
                if args.max_cases is not None
                else args.batch_size
            )
            if remaining <= 0:
                break
            scan_limit = min(args.batch_size, remaining)
            report = scan_deadline_candidate_page(
                raw_root=get_settings().raw_data_root,
                as_of=args.as_of,
                after_serial=cursor,
                scan_limit=scan_limit,
                result_limit=MAX_RESULT_BUFFER,
                horizon_days=args.horizon_days,
                recent_past_days=args.recent_past_days,
            )
            if report["result_truncated"]:
                raise RuntimeError(
                    "Candidate buffer truncated a case page; lower --batch-size before retrying"
                )
            page_count += 1
            scanned_total += int(report["scanned_case_count"])
            role_state = report["event_role_state"]
            for candidate in report["candidates"]:
                handle.write(
                    json.dumps(candidate, ensure_ascii=False, default=str) + "\n"
                )
                candidate_total += 1

            next_cursor = str(report["last_scanned_serial"] or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
            if not report["has_more_cases"]:
                break
            if args.max_cases is not None and scanned_total >= args.max_cases:
                break

    manifest = {
        "status": "COMPLETE",
        "output": str(args.output),
        "as_of": args.as_of,
        "horizon_days": args.horizon_days,
        "recent_past_days": args.recent_past_days,
        "page_count": page_count,
        "scanned_case_count": scanned_total,
        "candidate_count": candidate_total,
        "last_scanned_serial": cursor,
        "event_role_state": role_state,
        "semantics": "DEADLINE_CANDIDATES_NOT_LEGAL_STATUS_OR_FINAL_DOCKET",
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
