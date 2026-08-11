from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Any, Iterable


PREFLIGHT_VERSION = "USPTO_ODP_BULK_METADATA_PREFLIGHT_V1"
PRODUCT_IDENTIFIERS = {
    "assignment": "trtdxfag",
    "ttab": "ttabtdxf",
}
_FILE_NAME_KEYS = ("fileName", "filename", "file_name")
_DATE_KEYS = ("fileDate", "releaseDate", "file_date", "release_date")
_TIMESTAMP_KEYS = (
    "snapshotAt",
    "releaseDateTime",
    "fileDateTime",
    "snapshot_at",
    "release_datetime",
    "file_datetime",
)
_PRODUCT_KEYS = ("productIdentifier", "product_identifier", "productId", "product_id")


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return key, value.strip()
    return None, None


def _basename(value: str) -> str:
    return Path(value.replace("\\", "/")).name


def _parse_date(value: str) -> str | None:
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _parse_timezone_aware_timestamp(value: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.isoformat(timespec="milliseconds")


def _product_identifiers(payload: Any) -> set[str]:
    values: set[str] = set()
    for row in _walk_dicts(payload):
        _, value = _first_text(row, _PRODUCT_KEYS)
        if value:
            values.add(value)
    return values


def _matching_rows(payload: Any, expected_file_name: str) -> list[dict[str, Any]]:
    expected = _basename(expected_file_name)
    matches: list[dict[str, Any]] = []
    for row in _walk_dicts(payload):
        _, value = _first_text(row, _FILE_NAME_KEYS)
        if value and _basename(value) == expected:
            matches.append(row)
    return matches


def _assignment_source(row: dict[str, Any], expected_file_name: str) -> tuple[dict[str, Any] | None, str | None]:
    date_key, date_value = _first_text(row, _DATE_KEYS)
    if not date_value:
        timestamp_key, timestamp_value = _first_text(row, _TIMESTAMP_KEYS)
        if timestamp_value:
            effective_date = _parse_date(timestamp_value)
            if effective_date:
                return (
                    {
                        "file_name": _basename(expected_file_name),
                        "effective_date": effective_date,
                        "metadata_field": timestamp_key,
                    },
                    None,
                )
        return None, "AUTHORITATIVE_EFFECTIVE_DATE_MISSING"
    effective_date = _parse_date(date_value)
    if not effective_date:
        return None, "AUTHORITATIVE_EFFECTIVE_DATE_INVALID"
    return (
        {
            "file_name": _basename(expected_file_name),
            "effective_date": effective_date,
            "metadata_field": date_key,
        },
        None,
    )


def _ttab_source(row: dict[str, Any], expected_file_name: str) -> tuple[dict[str, Any] | None, str | None]:
    timestamp_key, timestamp_value = _first_text(row, _TIMESTAMP_KEYS)
    if timestamp_value:
        snapshot_at = _parse_timezone_aware_timestamp(timestamp_value)
        if snapshot_at:
            return (
                {
                    "file_name": _basename(expected_file_name),
                    "snapshot_at": snapshot_at,
                    "metadata_field": timestamp_key,
                },
                None,
            )
        return None, "AUTHORITATIVE_TIMESTAMP_NOT_TIMEZONE_AWARE"

    _, date_value = _first_text(row, _DATE_KEYS)
    if date_value and _parse_date(date_value):
        return None, "AUTHORITATIVE_TIMESTAMP_PRECISION_MISSING"
    return None, "AUTHORITATIVE_SNAPSHOT_AT_MISSING"


def evaluate_metadata(
    *,
    domain: str,
    metadata: Any,
    expected_file_names: list[str],
) -> dict[str, Any]:
    normalized_domain = domain.strip().lower()
    if normalized_domain not in PRODUCT_IDENTIFIERS:
        raise ValueError(f"domain must be one of {sorted(PRODUCT_IDENTIFIERS)}")
    expected_product = PRODUCT_IDENTIFIERS[normalized_domain]

    issues: list[dict[str, Any]] = []
    product_ids = _product_identifiers(metadata)
    if product_ids and expected_product not in product_ids:
        issues.append(
            {
                "type": "ODP_PRODUCT_IDENTIFIER_MISMATCH",
                "expected": expected_product,
                "observed": sorted(product_ids),
            }
        )

    if not expected_file_names:
        issues.append({"type": "EXPECTED_FILE_NAMES_REQUIRED"})

    seen_expected: set[str] = set()
    plan: list[dict[str, Any]] = []
    extractor = _assignment_source if normalized_domain == "assignment" else _ttab_source
    for raw_name in expected_file_names:
        file_name = _basename(raw_name)
        if file_name in seen_expected:
            issues.append({"type": "DUPLICATE_EXPECTED_FILE_NAME", "file_name": file_name})
            continue
        seen_expected.add(file_name)
        matches = _matching_rows(metadata, file_name)
        if not matches:
            issues.append({"type": "ODP_FILE_METADATA_NOT_FOUND", "file_name": file_name})
            continue
        if len(matches) > 1:
            issues.append(
                {
                    "type": "ODP_FILE_METADATA_AMBIGUOUS",
                    "file_name": file_name,
                    "match_count": len(matches),
                }
            )
            continue
        source, error = extractor(matches[0], file_name)
        if error:
            issues.append({"type": error, "file_name": file_name})
            continue
        assert source is not None
        plan.append(source)

    safe = not issues and len(plan) == len(seen_expected) and bool(plan)
    return {
        "preflight_version": PREFLIGHT_VERSION,
        "status": "READY" if safe else "NOT_READY",
        "safe": safe,
        "domain": normalized_domain,
        "odp_product_identifier": expected_product,
        "metadata_product_identifiers_observed": sorted(product_ids),
        "expected_file_count": len(seen_expected),
        "resolved_file_count": len(plan),
        "issues": issues,
        "plan": plan,
        "effective_date_inferred_from_filename": False,
        "snapshot_at_inferred_from_filename": False,
        "timestamp_midnight_manufactured_from_date": False,
        "metadata_policy": (
            "Use explicit authoritative ODP release/file metadata only. Filename dates are never "
            "parsed. Assignment may use an explicit authoritative date. TTAB requires an explicit "
            "timezone-aware timestamp; date-only metadata remains NOT_READY."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate saved USPTO Open Data Portal bulk product metadata without filename inference"
    )
    parser.add_argument("--stdin", action="store_true")
    args = parser.parse_args()
    if not args.stdin:
        parser.error("--stdin is required")
    payload = json.load(sys.stdin)
    report = evaluate_metadata(
        domain=str(payload.get("domain") or ""),
        metadata=payload.get("metadata"),
        expected_file_names=[str(value) for value in (payload.get("expected_file_names") or [])],
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
