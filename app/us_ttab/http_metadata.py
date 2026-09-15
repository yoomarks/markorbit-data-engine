from __future__ import annotations

from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from app.us_ttab.corpus_manifest import DAILY_KIND, HISTORICAL_KIND, resolve_source_path


HTTP_EVIDENCE_VERSION = "USPTO_TTAB_HTTP_METADATA_EVIDENCE_V1"
_PRODUCT_BY_KIND = {
    HISTORICAL_KIND: "TTABYR",
    DAILY_KIND: "TTABTDXF",
}


def _text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _content_length(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _timestamp(value: str) -> str | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_url_ok(url: str, *, product: str, file_name: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or parsed.hostname != "data.uspto.gov":
        return False
    expected = f"/files/{product}/{file_name}"
    return unquote(parsed.path) == expected


def evaluate_ttab_http_evidence(
    *,
    evidence: Any,
    source_specs: Any,
    raw_root: Path,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if not isinstance(evidence, list) or not evidence:
        return {
            "version": HTTP_EVIDENCE_VERSION,
            "status": "NOT_READY",
            "safe": False,
            "issues": [{"type": "HTTP_EVIDENCE_REQUIRED"}],
            "normalized_metadata": None,
        }
    if not isinstance(source_specs, list) or not source_specs:
        issues.append({"type": "SOURCE_SPECS_REQUIRED"})
        source_specs = []

    by_name: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(evidence):
        if not isinstance(row, dict):
            issues.append({"type": "HTTP_EVIDENCE_ROW_INVALID", "index": index})
            continue
        name = _text(row, "file_name", "fileName", "filename")
        if not name:
            issues.append({"type": "HTTP_EVIDENCE_FILE_NAME_MISSING", "index": index})
            continue
        name = Path(name.replace("\\", "/")).name
        if name in by_name:
            issues.append({"type": "HTTP_EVIDENCE_DUPLICATE_FILE", "file_name": name})
            continue
        by_name[name] = row

    plan: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, str]]] = {"TTABYR": [], "TTABTDXF": []}
    for index, spec in enumerate(source_specs):
        if not isinstance(spec, dict):
            issues.append({"type": "SOURCE_SPEC_INVALID", "index": index})
            continue
        source_kind = _text(spec, "source_kind").upper()
        product = _PRODUCT_BY_KIND.get(source_kind)
        path_text = _text(spec, "path")
        if not product or not path_text:
            issues.append({"type": "SOURCE_SPEC_INVALID", "index": index})
            continue
        file_name = Path(path_text.replace("\\", "/")).name
        row = by_name.get(file_name)
        if row is None:
            issues.append({"type": "HTTP_EVIDENCE_FILE_NOT_FOUND", "file_name": file_name})
            continue
        resolved = resolve_source_path(raw_root, path_text)
        if not resolved.is_file():
            issues.append({"type": "SOURCE_FILE_NOT_FOUND", "file_name": file_name})
            continue

        url = _text(row, "url", "canonical_url", "host_url")
        if not _canonical_url_ok(url, product=product, file_name=file_name):
            issues.append({"type": "HTTP_EVIDENCE_URL_MISMATCH", "file_name": file_name})
            continue
        etag = _text(row, "etag", "eTag")
        if not etag:
            issues.append({"type": "HTTP_EVIDENCE_ETAG_MISSING", "file_name": file_name})
            continue
        last_modified = _text(row, "last_modified", "lastModified")
        snapshot_at = _timestamp(last_modified)
        if snapshot_at is None:
            issues.append({"type": "HTTP_EVIDENCE_TIMESTAMP_INVALID", "file_name": file_name})
            continue
        length = _content_length(row.get("content_length", row.get("contentLength")))
        if length is None:
            issues.append({"type": "HTTP_EVIDENCE_CONTENT_LENGTH_INVALID", "file_name": file_name})
            continue
        actual_size = resolved.stat().st_size
        if length != actual_size:
            issues.append(
                {
                    "type": "HTTP_EVIDENCE_SIZE_MISMATCH",
                    "file_name": file_name,
                    "expected": actual_size,
                    "observed": length,
                }
            )
            continue
        grouped[product].append(
            {"fileName": file_name, "releaseDateTime": snapshot_at}
        )
        plan.append(
            {
                "file_name": file_name,
                "source_kind": source_kind,
                "product_identifier": product.lower(),
                "snapshot_at": snapshot_at,
                "etag": etag,
                "content_length": length,
                "url": url,
            }
        )

    normalized_metadata = [
        {"productIdentifier": product.lower(), "files": rows}
        for product, rows in grouped.items()
        if rows
    ]
    expected_count = len(source_specs)
    safe = not issues and len(plan) == expected_count and expected_count > 0
    return {
        "version": HTTP_EVIDENCE_VERSION,
        "status": "READY" if safe else "NOT_READY",
        "safe": safe,
        "expected_file_count": expected_count,
        "resolved_file_count": len(plan),
        "issues": issues,
        "plan": plan,
        "normalized_metadata": normalized_metadata if safe else None,
        "source_kind_inferred_from_filename": False,
        "publication_timestamp_inferred_from_filename": False,
        "local_mtime_used_as_authority": False,
    }


def main() -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Validate official USPTO TTAB HTTP file metadata evidence"
    )
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--raw-root", type=Path, required=True)
    args = parser.parse_args()
    if not args.stdin:
        parser.error("--stdin is required")
    payload = json.load(sys.stdin)
    report = evaluate_ttab_http_evidence(
        evidence=payload.get("evidence"),
        source_specs=payload.get("sources"),
        raw_root=args.raw_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
