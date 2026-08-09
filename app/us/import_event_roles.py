from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.us.event_roles import import_event_role_ruleset, load_event_role_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import a reviewed USPTO event-code to MarkOrbit deadline-role ruleset. "
            "Production import requires the local evidence file beside the JSON payload."
        )
    )
    parser.add_argument("--ruleset-file", type=Path, required=True)
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument(
        "--skip-source-file-verification",
        action="store_true",
        help="Test-only escape hatch; do not use for production imports.",
    )
    args = parser.parse_args()
    normalized = load_event_role_payload(
        args.ruleset_file,
        verify_source_file=not args.skip_source_file_verification,
    )
    result = import_event_role_ruleset(normalized, activate=not args.no_activate)
    result["ruleset_file"] = str(args.ruleset_file)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
