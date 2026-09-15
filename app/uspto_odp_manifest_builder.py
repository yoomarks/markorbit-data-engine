from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any

from app.us_assignment.corpus_manifest import (
    DAILY_KIND as ASSIGNMENT_DAILY_KIND,
    MANIFEST_VERSION as ASSIGNMENT_MANIFEST_VERSION,
    SNAPSHOT_KIND as ASSIGNMENT_SNAPSHOT_KIND,
)
from app.us_ttab.corpus_manifest import (
    DAILY_KIND as TTAB_DAILY_KIND,
    HISTORICAL_KIND as TTAB_HISTORICAL_KIND,
    MANIFEST_VERSION as TTAB_MANIFEST_VERSION,
)
from app.uspto_odp_bulk_metadata import evaluate_metadata


BUILDER_VERSION = "USPTO_ODP_CORPUS_MANIFEST_BUILDER_V1"
DOMAIN_POLICY = {
    "assignment": {
        "historical_kind": ASSIGNMENT_SNAPSHOT_KIND,
        "daily_kind": ASSIGNMENT_DAILY_KIND,
        "manifest_version": ASSIGNMENT_MANIFEST_VERSION,
        "domain_dir": "us_assignment",
        "timestamp_field": "effective_date",
    },
    "ttab": {
        "historical_kind": TTAB_HISTORICAL_KIND,
        "daily_kind": TTAB_DAILY_KIND,
        "manifest_version": TTAB_MANIFEST_VERSION,
        "domain_dir": "us_ttab",
        "timestamp_field": "snapshot_at",
    },
}


def _normalize_manifest_path(value: object, *, domain_dir: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"sources[{index}].path is required")
    text = value.strip().replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"sources[{index}].path must be relative and stay inside RAW_DATA_PATH")
    if len(path.parts) < 3 or path.parts[0] not in {"incoming", "archive"} or path.parts[1] != domain_dir:
        raise ValueError(
            f"sources[{index}].path must be under incoming/{domain_dir}/ or archive/{domain_dir}/"
        )
    if not path.name:
        raise ValueError(f"sources[{index}].path must name a source file")
    return str(path)


def _parse_source_specs(domain: str, source_specs: Any) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    policy = DOMAIN_POLICY[domain]
    if not isinstance(source_specs, list) or not source_specs:
        return [], [{"type": "SOURCE_SPECS_REQUIRED"}]

    allowed_kinds = {policy["historical_kind"], policy["daily_kind"]}
    normalized: list[dict[str, str]] = []
    issues: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_basenames: set[str] = set()
    for index, item in enumerate(source_specs):
        if not isinstance(item, dict):
            issues.append({"type": "SOURCE_SPEC_INVALID", "index": index})
            continue
        try:
            path = _normalize_manifest_path(item.get("path"), domain_dir=policy["domain_dir"], index=index)
        except ValueError as exc:
            issues.append({"type": "SOURCE_PATH_INVALID", "index": index, "error": str(exc)})
            continue
        kind_raw = item.get("source_kind")
        if not isinstance(kind_raw, str) or kind_raw.strip().upper() not in allowed_kinds:
            issues.append(
                {
                    "type": "SOURCE_KIND_INVALID",
                    "index": index,
                    "path": path,
                    "allowed": sorted(allowed_kinds),
                }
            )
            continue
        source_kind = kind_raw.strip().upper()
        basename = Path(path).name
        if path in seen_paths:
            issues.append({"type": "DUPLICATE_SOURCE_PATH", "path": path})
            continue
        if basename in seen_basenames:
            issues.append({"type": "DUPLICATE_SOURCE_BASENAME", "file_name": basename})
            continue
        seen_paths.add(path)
        seen_basenames.add(basename)
        normalized.append({"path": path, "file_name": basename, "source_kind": source_kind})

    historical = [row for row in normalized if row["source_kind"] == policy["historical_kind"]]
    if domain == "assignment" and len(historical) != 1:
        issues.append(
            {
                "type": "HISTORICAL_SOURCE_COUNT_MISMATCH",
                "expected": 1,
                "observed": len(historical),
            }
        )
    elif domain == "ttab" and not historical:
        issues.append(
            {
                "type": "HISTORICAL_SOURCE_COUNT_MISMATCH",
                "expected_minimum": 1,
                "observed": 0,
            }
        )
    return normalized, issues


def _utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("snapshot_at must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _build_assignment_manifest(
    specs: list[dict[str, str]], metadata_plan: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    by_name = {str(row["file_name"]): row for row in metadata_plan}
    rows: list[dict[str, str]] = []
    issues: list[dict[str, Any]] = []
    seen_dates: dict[str, str] = {}
    historical_date: str | None = None
    daily_dates: list[str] = []

    for spec in specs:
        metadata = by_name.get(spec["file_name"])
        if metadata is None:
            issues.append({"type": "METADATA_PLAN_SOURCE_MISSING", "file_name": spec["file_name"]})
            continue
        effective_date = str(metadata["effective_date"])
        if effective_date in seen_dates:
            issues.append(
                {
                    "type": "DUPLICATE_EFFECTIVE_DATE_NOT_MODELED",
                    "effective_date": effective_date,
                    "files": sorted([seen_dates[effective_date], spec["file_name"]]),
                }
            )
        seen_dates[effective_date] = spec["file_name"]
        if spec["source_kind"] == ASSIGNMENT_SNAPSHOT_KIND:
            historical_date = effective_date
        else:
            daily_dates.append(effective_date)
        rows.append(
            {
                "path": spec["path"],
                "source_kind": spec["source_kind"],
                "effective_date": effective_date,
            }
        )

    if historical_date is not None:
        invalid_daily = sorted(value for value in daily_dates if value <= historical_date)
        if invalid_daily:
            issues.append(
                {
                    "type": "DAILY_NOT_AFTER_HISTORICAL_SNAPSHOT",
                    "historical_effective_date": historical_date,
                    "daily_dates": invalid_daily,
                }
            )
    if issues:
        return None, issues

    rows.sort(key=lambda row: (row["effective_date"], row["source_kind"], row["path"]))
    return (
        {
            "manifest_version": ASSIGNMENT_MANIFEST_VERSION,
            "expected_snapshot_packages": 1,
            "expected_daily_packages": len(daily_dates),
            "daily_through": max(daily_dates) if daily_dates else None,
            "sources": rows,
        },
        [],
    )


def _build_ttab_manifest(
    specs: list[dict[str, str]], metadata_plan: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    by_name = {str(row["file_name"]): row for row in metadata_plan}
    rows: list[dict[str, str]] = []
    issues: list[dict[str, Any]] = []
    historical_count = 0
    daily_count = 0

    for spec in specs:
        metadata = by_name.get(spec["file_name"])
        if metadata is None:
            issues.append({"type": "METADATA_PLAN_SOURCE_MISSING", "file_name": spec["file_name"]})
            continue
        snapshot_at = _utc_timestamp(str(metadata["snapshot_at"]))
        if spec["source_kind"] == TTAB_HISTORICAL_KIND:
            historical_count += 1
        else:
            daily_count += 1
        rows.append(
            {
                "path": spec["path"],
                "source_kind": spec["source_kind"],
                "snapshot_at": snapshot_at,
            }
        )

    if issues:
        return None, issues

    # Publication timestamps are provenance only. Logical TTAB replay precedence
    # and daily_through are derived later from authoritative XML transaction-date
    # during raw-source preflight; never infer them from publication time.
    rows.sort(key=lambda row: (0 if row["source_kind"] == TTAB_HISTORICAL_KIND else 1, row["path"]))
    return (
        {
            "manifest_version": TTAB_MANIFEST_VERSION,
            "expected_historical_packages": historical_count,
            "expected_daily_packages": daily_count,
            "daily_through": None,
            "sources": rows,
        },
        [],
    )



def _ttab_metadata_preflight(
    specs: list[dict[str, str]], metadata: Any
) -> dict[str, Any]:
    payloads = metadata if isinstance(metadata, list) else [metadata]
    identity = {
        TTAB_HISTORICAL_KIND: "ttabyr",
        TTAB_DAILY_KIND: "ttabtdxf",
    }
    plan: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []

    for spec in specs:
        expected_slug = identity[spec["source_kind"]]
        product_reports: list[dict[str, Any]] = []
        wrong_product_matches: list[list[str]] = []
        for payload in payloads:
            report = evaluate_metadata(
                domain="ttab",
                metadata=payload,
                expected_file_names=[spec["file_name"]],
            )
            observed = set(report.get("metadata_product_identifiers_observed") or [])
            if expected_slug in observed:
                product_reports.append(report)
            elif report.get("safe"):
                wrong_product_matches.append(sorted(observed))
            source_reports.append(
                {
                    "file_name": spec["file_name"],
                    "source_kind": spec["source_kind"],
                    "expected_product_identifier": expected_slug,
                    "observed_product_identifiers": sorted(observed),
                    "status": report.get("status"),
                }
            )

        if len(product_reports) != 1:
            issues.append(
                {
                    "type": "TTAB_ODP_PRODUCT_BINDING_NOT_UNIQUE",
                    "file_name": spec["file_name"],
                    "source_kind": spec["source_kind"],
                    "expected_product_identifier": expected_slug,
                    "match_count": len(product_reports),
                }
            )
            continue

        report = product_reports[0]
        if not report.get("safe"):
            report_issues = report.get("issues") or []
            missing_expected = any(
                item.get("type") == "ODP_FILE_METADATA_NOT_FOUND" for item in report_issues
            )
            if missing_expected and wrong_product_matches:
                issues.append(
                    {
                        "type": "TTAB_ODP_PRODUCT_BINDING_MISMATCH",
                        "file_name": spec["file_name"],
                        "source_kind": spec["source_kind"],
                        "expected_product_identifier": expected_slug,
                        "observed_wrong_product_identifiers": wrong_product_matches,
                    }
                )
            else:
                issues.extend(report_issues)
            continue
        source_plan = report.get("plan") or []
        if len(source_plan) != 1:
            issues.append(
                {
                    "type": "TTAB_ODP_SOURCE_METADATA_NOT_UNIQUE",
                    "file_name": spec["file_name"],
                    "source_kind": spec["source_kind"],
                    "match_count": len(source_plan),
                }
            )
            continue
        plan.append(source_plan[0])

    safe = not issues and len(plan) == len(specs) and bool(plan)
    return {
        "status": "READY" if safe else "NOT_READY",
        "safe": safe,
        "domain": "ttab",
        "expected_file_count": len(specs),
        "resolved_file_count": len(plan),
        "issues": issues,
        "plan": plan,
        "source_product_bindings": source_reports,
        "source_kind_inferred_from_filename": False,
        "source_time_inferred_from_filename": False,
    }

def build_manifest(*, domain: str, metadata: Any, source_specs: Any) -> dict[str, Any]:
    normalized_domain = domain.strip().lower()
    if normalized_domain not in DOMAIN_POLICY:
        raise ValueError(f"domain must be one of {sorted(DOMAIN_POLICY)}")

    specs, spec_issues = _parse_source_specs(normalized_domain, source_specs)
    expected_file_names = [row["file_name"] for row in specs]
    if normalized_domain == "ttab":
        metadata_preflight = _ttab_metadata_preflight(specs, metadata)
    else:
        metadata_preflight = evaluate_metadata(
            domain=normalized_domain,
            metadata=metadata,
            expected_file_names=expected_file_names,
        )
    issues = list(spec_issues)
    issues.extend(metadata_preflight.get("issues") or [])
    if issues:
        return {
            "builder_version": BUILDER_VERSION,
            "status": "NOT_READY",
            "safe": False,
            "domain": normalized_domain,
            "issues": issues,
            "manifest": None,
            "metadata_preflight": metadata_preflight,
            "source_kind_inferred_from_filename": False,
            "source_time_inferred_from_filename": False,
        }

    if normalized_domain == "assignment":
        manifest, build_issues = _build_assignment_manifest(specs, metadata_preflight["plan"])
    else:
        manifest, build_issues = _build_ttab_manifest(specs, metadata_preflight["plan"])
    if build_issues:
        return {
            "builder_version": BUILDER_VERSION,
            "status": "NOT_READY",
            "safe": False,
            "domain": normalized_domain,
            "issues": build_issues,
            "manifest": None,
            "metadata_preflight": metadata_preflight,
            "source_kind_inferred_from_filename": False,
            "source_time_inferred_from_filename": False,
        }

    return {
        "builder_version": BUILDER_VERSION,
        "status": "READY",
        "safe": True,
        "domain": normalized_domain,
        "issues": [],
        "manifest": manifest,
        "metadata_preflight": metadata_preflight,
        "source_kind_inferred_from_filename": False,
        "source_time_inferred_from_filename": False,
        "semantics": (
            "TTAB publication timestamps come only from explicit authoritative metadata; "
            "logical corpus precedence is verified from raw XML transaction-date during source "
            "preflight. Historical-vs-daily source kind is explicit input and is never inferred "
            "from filenames."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Assignment/TTAB corpus manifests from explicit source kinds and ODP metadata"
    )
    parser.add_argument("--stdin", action="store_true")
    args = parser.parse_args()
    if not args.stdin:
        parser.error("--stdin is required")
    payload = json.load(sys.stdin)
    report = build_manifest(
        domain=str(payload.get("domain") or ""),
        metadata=payload.get("metadata"),
        source_specs=payload.get("sources"),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
