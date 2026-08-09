from __future__ import annotations

import argparse
from datetime import date
import json

from app.us.maintenance import calculate_maintenance_schedule


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate USPTO maintenance windows without inferring registration legal status"
    )
    parser.add_argument("--registration-date", type=date.fromisoformat, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--madrid-66a", action="store_true")
    parser.add_argument("--international-registration-date", type=date.fromisoformat)
    parser.add_argument("--current-term-expiration-date", type=date.fromisoformat)
    args = parser.parse_args()
    report = calculate_maintenance_schedule(
        registration_date=args.registration_date,
        as_of=args.as_of,
        madrid_66a=args.madrid_66a,
        international_registration_date=args.international_registration_date,
        current_term_expiration_date=args.current_term_expiration_date,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
