from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from app.tmclass.repository import admit_tmclass_evidence


BUNDLE_VERSION = "TMCLASS_SOURCE_EVIDENCE_BUNDLE_V1"


def load_bundle(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("TMclass evidence bundle must be an object")
    if (
        value.get("contractVersion") != BUNDLE_VERSION
        or value.get("objectType") != "TMCLASS_SOURCE_EVIDENCE_BUNDLE"
    ):
        raise ValueError("TMclass evidence bundle identity is invalid")
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        raise ValueError("TMclass evidence bundle evidence must be an array of objects")
    if value.get("evidenceCount") != len(evidence):
        raise ValueError("TMclass evidence bundle count does not match its contents")
    return evidence


def import_bundle(
    evidence: list[dict[str, Any]],
    admit: Callable[[Mapping[str, Any]], dict[str, Any]] = admit_tmclass_evidence,
) -> dict[str, Any]:
    receipts = [admit(package) for package in evidence]
    page_kinds: dict[str, int] = {}
    for receipt in receipts:
        page_kind = str(receipt.get("page_kind", ""))
        page_kinds[page_kind] = page_kinds.get(page_kind, 0) + 1
    return {
        "outcome": "TMCLASS_EVIDENCE_BUNDLE_ADMITTED",
        "evidence_count": len(receipts),
        "replayed_count": sum(bool(receipt.get("replayed")) for receipt in receipts),
        "page_kinds": page_kinds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Admit a Knowledge TMclass evidence bundle")
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    print(json.dumps(import_bundle(load_bundle(args.bundle)), sort_keys=True))


if __name__ == "__main__":
    main()
