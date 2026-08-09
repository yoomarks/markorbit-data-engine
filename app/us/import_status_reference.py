from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.us.status_reference import import_reference_payload, load_reference_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a normalized official USPTO trademark status-code reference payload"
    )
    parser.add_argument(
        "--reference-file",
        type=Path,
        required=True,
        help="Path to MARKORBIT_USPTO_STATUS_REFERENCE_V1 JSON payload.",
    )
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="Import the version without making it the active read-time reference.",
    )
    args = parser.parse_args()
    normalized = load_reference_payload(args.reference_file)
    result = import_reference_payload(normalized, activate=not args.no_activate)
    result["reference_file"] = str(args.reference_file)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
