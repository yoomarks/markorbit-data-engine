from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import get_settings
from app.us_assignment.corpus_manifest import preflight_manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only preflight for an explicit USPTO Assignment corpus manifest"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    report = preflight_manifest(args.manifest, get_settings().raw_data_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("safe") else 2


if __name__ == "__main__":
    raise SystemExit(main())
