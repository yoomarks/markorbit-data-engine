from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from app.tmclass.import_cli import import_bundle, load_bundle
from app.tmclass.repository import admit_tmclass_evidence


RECEIPT_VERSION = "TMCLASS_CORPUS_IMPORT_RECEIPT_V1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.bundle.json") if path.is_file())


def receipt_path(bundle_root: Path, receipt_root: Path, bundle: Path) -> Path:
    relative = bundle.relative_to(bundle_root)
    return receipt_root / relative.with_suffix(relative.suffix + ".receipt.json")


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def accepted_receipt(path: Path, expected_sha256: str) -> bool:
    if not path.exists():
        return False
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != RECEIPT_VERSION
        or value.get("bundle_sha256") != expected_sha256
        or value.get("outcome") != "TMCLASS_EVIDENCE_BUNDLE_ADMITTED"
    ):
        raise ValueError(f"TMclass corpus receipt does not match its bundle: {path}")
    return True


def import_corpus_pass(
    bundle_root: Path,
    receipt_root: Path,
    admit: Callable[[Mapping[str, Any]], dict[str, Any]] = admit_tmclass_evidence,
) -> dict[str, int]:
    admitted_batches = 0
    admitted_evidence = 0
    replayed_evidence = 0
    skipped_batches = 0
    for bundle in bundle_files(bundle_root):
        bundle_sha256 = sha256_file(bundle)
        receipt = receipt_path(bundle_root, receipt_root, bundle)
        if accepted_receipt(receipt, bundle_sha256):
            skipped_batches += 1
            continue
        result = import_bundle(load_bundle(bundle), admit)
        atomic_write_json(
            receipt,
            {
                "schema_version": RECEIPT_VERSION,
                "outcome": result["outcome"],
                "admitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "bundle": str(bundle.resolve()),
                "bundle_sha256": bundle_sha256,
                "evidence_count": result["evidence_count"],
                "replayed_count": result["replayed_count"],
                "page_kinds": result["page_kinds"],
            },
        )
        admitted_batches += 1
        admitted_evidence += int(result["evidence_count"])
        replayed_evidence += int(result["replayed_count"])
        print(
            json.dumps(
                {
                    "outcome": "TMCLASS_CORPUS_BATCH_ADMITTED",
                    "bundle": str(bundle),
                    **result,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    return {
        "admitted_batches": admitted_batches,
        "admitted_evidence": admitted_evidence,
        "replayed_evidence": replayed_evidence,
        "skipped_batches": skipped_batches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Continuously admit Knowledge TMclass corpus evidence bundles"
    )
    parser.add_argument("bundle_root", type=Path)
    parser.add_argument("--receipt-root", required=True, type=Path)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--capture-complete", type=Path)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()
    if args.poll_seconds < 1 or args.poll_seconds > 300:
        parser.error("--poll-seconds must be in 1..300")
    if args.continuous and args.capture_complete is None:
        parser.error("--capture-complete is required with --continuous")

    while True:
        result = import_corpus_pass(args.bundle_root.resolve(), args.receipt_root.resolve())
        print(json.dumps({"outcome": "TMCLASS_CORPUS_IMPORT_PASS", **result}, sort_keys=True))
        if not args.continuous or args.capture_complete.exists():
            return
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
