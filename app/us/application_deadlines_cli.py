from __future__ import annotations

import argparse
from datetime import date
import json

from app.us.application_deadlines import calculate_application_deadlines


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate evidence-backed USPTO application-stage deadlines without "
            "inferring application legal status"
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--madrid-66a", action="store_true")
    parser.add_argument("--publication-date", type=date.fromisoformat)
    parser.add_argument("--office-action-issue-date", type=date.fromisoformat)
    parser.add_argument("--office-action-final", action="store_true")
    parser.add_argument("--office-action-notice-deadline", type=date.fromisoformat)
    parser.add_argument("--notice-of-allowance-date", type=date.fromisoformat)
    parser.add_argument("--itu-extensions-granted", type=int, choices=range(0, 6))
    parser.add_argument("--statement-of-use-filed", action="store_true")
    parser.add_argument(
        "--opposition-extension-days-granted",
        type=int,
        choices=(0, 30, 90, 150),
    )
    args = parser.parse_args()

    report = calculate_application_deadlines(
        as_of=args.as_of,
        madrid_66a=args.madrid_66a,
        publication_date=args.publication_date,
        office_action_issue_date=args.office_action_issue_date,
        office_action_final=args.office_action_final,
        office_action_notice_deadline=args.office_action_notice_deadline,
        notice_of_allowance_date=args.notice_of_allowance_date,
        itu_extensions_granted=args.itu_extensions_granted,
        statement_of_use_filed=args.statement_of_use_filed,
        opposition_extension_days_granted=args.opposition_extension_days_granted,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
