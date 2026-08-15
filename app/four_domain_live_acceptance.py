from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.four_domain_acceptance import (
    build_archive_evidence,
    evaluate_four_domain_acceptance,
)


LIVE_ACCEPTANCE_VERSION = "MARKORBIT_FOUR_DOMAIN_LIVE_ACCEPTANCE_V1"


def _validate_daily_through(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("expected_application_daily_through is required")
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "expected_application_daily_through must be an ISO date (YYYY-MM-DD)"
        ) from exc
    if parsed.isoformat() != normalized:
        raise ValueError(
            "expected_application_daily_through must use canonical YYYY-MM-DD format"
        )
    return normalized


def _report_sha256(report: dict[str, Any]) -> str:
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _default_cn_builder() -> dict[str, Any]:
    from app.cn.audit_acceptance import build_acceptance_audit

    return build_acceptance_audit()


def _default_application_builder(
    *, verify_source_files: bool, expected_history_parts: int
) -> dict[str, Any]:
    # Import the M1.4 wrapper, not the M1.3 core. The wrapper pins the formal
    # audit identity while reusing the existing history-part acceptance logic.
    from app.us import audit_real_data_v2

    return audit_real_data_v2.build_audit(
        verify_source_files=verify_source_files,
        expected_history_parts=expected_history_parts,
    )


def _default_assignment_builder(manifest_path: Path, raw_root: Path) -> dict[str, Any]:
    from app.us_assignment.corpus_audit import build_manifest_acceptance

    return build_manifest_acceptance(manifest_path, raw_root)


def _default_ttab_builder(manifest_path: Path, raw_root: Path) -> dict[str, Any]:
    from app.us_ttab.corpus_audit import build_manifest_acceptance

    return build_manifest_acceptance(manifest_path, raw_root)


def build_live_acceptance(
    raw_root: Path,
    *,
    expected_application_history_parts: int,
    expected_application_daily_through: str,
    cn_builder: Callable[[], dict[str, Any]] = _default_cn_builder,
    application_builder: Callable[..., dict[str, Any]] = _default_application_builder,
    assignment_builder: Callable[[Path, Path], dict[str, Any]] = _default_assignment_builder,
    ttab_builder: Callable[[Path, Path], dict[str, Any]] = _default_ttab_builder,
    archive_builder: Callable[[], dict[str, Any]] = build_archive_evidence,
) -> dict[str, Any]:
    """Generate fresh read-only evidence for the frozen four-domain final gate.

    This function deliberately performs no ingestion and does not infer the
    Application coverage policy. The caller must explicitly pin both the
    historical part count and trailing daily coverage date.
    """
    if expected_application_history_parts < 1:
        raise ValueError("expected_application_history_parts must be at least 1")
    daily_through = _validate_daily_through(expected_application_daily_through)
    root = Path(raw_root)
    assignment_manifest = root / "manifests" / "us_assignment" / "corpus.json"
    ttab_manifest = root / "manifests" / "us_ttab" / "corpus.json"

    reports = {
        "cn": cn_builder(),
        "application": application_builder(
            verify_source_files=True,
            expected_history_parts=expected_application_history_parts,
        ),
        "assignment": assignment_builder(assignment_manifest, root),
        "ttab": ttab_builder(ttab_manifest, root),
    }
    policy = {
        "expected_application_history_parts": expected_application_history_parts,
        "expected_application_daily_through": daily_through,
    }
    bundle = {"policy": policy, "reports": reports, "report_files": {}}
    archive_evidence = archive_builder()
    formal = evaluate_four_domain_acceptance(bundle, archive_evidence)

    return {
        "live_acceptance_version": LIVE_ACCEPTANCE_VERSION,
        "read_only": True,
        "status": formal["status"],
        "policy": policy,
        "source_manifests": {
            "assignment": str(assignment_manifest),
            "ttab": str(ttab_manifest),
        },
        "report_integrity_sha256": {
            domain: _report_sha256(report) for domain, report in reports.items()
        },
        "reports": reports,
        "archive_evidence": archive_evidence,
        "formal_acceptance": formal,
        "semantics": {
            "cross_domain_legal_event_order_inference": False,
            "assignment_recordation_is_ownership_conclusion": False,
            "ttab_procedure_is_substantive_outcome_conclusion": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate fresh CN/Application/Assignment/TTAB evidence and run the formal "
            "read-only four-domain final acceptance gate"
        )
    )
    parser.add_argument(
        "--expected-application-history-parts",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--expected-application-daily-through",
        required=True,
        help="Explicit pinned daily coverage end in YYYY-MM-DD format.",
    )
    parser.add_argument("--raw-root", type=Path, default=None)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    if args.expected_application_history_parts < 1:
        parser.error("--expected-application-history-parts must be at least 1")

    report = build_live_acceptance(
        args.raw_root or get_settings().raw_data_root,
        expected_application_history_parts=args.expected_application_history_parts,
        expected_application_daily_through=args.expected_application_daily_through,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            default=str,
        )
    )
    return 0 if report["status"] in {"PASS", "PASS_WITH_WARNINGS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
