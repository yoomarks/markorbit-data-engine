from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.us.status_interpretation import import_ruleset, load_ruleset_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import an evidence-bound MarkOrbit US status interpretation ruleset"
    )
    parser.add_argument("--ruleset-file", type=Path, required=True)
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument(
        "--skip-source-file-verification",
        action="store_true",
        help="Test-only escape hatch; production imports must verify the ruleset evidence file.",
    )
    args = parser.parse_args()
    normalized = load_ruleset_payload(
        args.ruleset_file,
        verify_source_file=not args.skip_source_file_verification,
    )
    result = import_ruleset(normalized, activate=not args.no_activate)
    result["ruleset_file"] = str(args.ruleset_file)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
