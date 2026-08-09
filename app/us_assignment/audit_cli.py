from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import get_settings
from app.us_assignment.audit_real_data import build_audit
from app.us_assignment.readiness import build_readiness
from app.us_assignment.reconciliation import scan_reconciliation_page


def main() -> None:
    parser = argparse.ArgumentParser(description="US Assignment M1.0 read-only audit/report CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--verify-source-files", action="store_true")
    readiness = sub.add_parser("readiness")
    readiness.add_argument("--verify-source-files", action="store_true")
    reconcile = sub.add_parser("reconciliation")
    reconcile.add_argument("--after-serial", default="")
    reconcile.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    raw_root = Path(get_settings().raw_data_root)
    if args.command == "audit":
        result = build_audit(raw_root=raw_root, verify_sources=args.verify_source_files)
    elif args.command == "readiness":
        result = build_readiness(raw_root=raw_root, verify_sources=args.verify_source_files)
    else:
        result = scan_reconciliation_page(after_serial=args.after_serial, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
