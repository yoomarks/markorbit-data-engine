from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from app.us.reference_pack import build_reference_pack


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a source-hashed USPTO status/event reference JSON pack from an "
            "operator-reviewed CSV transcription. The tool does not parse or infer "
            "legal meanings from the source document."
        )
    )
    parser.add_argument("--family", choices=("status", "event"), required=True)
    parser.add_argument("--source-document", type=Path, required=True)
    parser.add_argument("--reviewed-csv", type=Path, required=True)
    parser.add_argument("--reference-version", required=True)
    parser.add_argument("--document-date", type=date.fromisoformat, required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--evidence-note", default="")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report = build_reference_pack(
        family=args.family,
        source_document=args.source_document,
        reviewed_csv=args.reviewed_csv,
        reference_version=args.reference_version,
        document_date=args.document_date,
        source_url=args.source_url,
        evidence_note=args.evidence_note,
        output_path=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
