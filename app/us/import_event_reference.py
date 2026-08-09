from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.us.event_reference import import_reference_payload, load_reference_payload
from app.us.reference_evidence import verify_payload_source_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a normalized official USPTO trademark event-code reference payload"
    )
    parser.add_argument("--reference-file", type=Path, required=True)
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument(
        "--skip-source-file-verification",
        action="store_true",
        help="Test-only escape hatch; production imports must verify the local official source file.",
    )
    args = parser.parse_args()
    normalized = load_reference_payload(args.reference_file)
    evidence = None
    if not args.skip_source_file_verification:
        evidence = verify_payload_source_file(normalized, args.reference_file)
    result = import_reference_payload(normalized, activate=not args.no_activate)
    result["reference_file"] = str(args.reference_file)
    result["source_evidence"] = evidence
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
