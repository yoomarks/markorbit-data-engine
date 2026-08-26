from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.cn.serving_state_checkpoint import (
    CHECKPOINT_VERSION as CN_SERVING_CHECKPOINT_VERSION,
)
from app.cn.serving_state_checkpoint import CRITICAL_TABLES as CN_CRITICAL_TABLES


PROMOTION_CONTRACT_VERSION = "MARKORBIT_M17_RELEASE_PROMOTION_V1"
OPERATOR_EVIDENCE_SCHEMA_VERSION = "CN_OPERATOR_RUNTIME_EVIDENCE_V1"
EXPECTED_OPERATOR_EVIDENCE_TYPE = "PRIOR_RUNTIME_VALIDATION_OPERATOR_ACCEPTED"
EXPECTED_OPERATOR_DECISION = "DO_NOT_RERUN_EXPENSIVE_PACKAGE_VALIDATION"
EXPECTED_SOURCE_PACKAGE = "2023_5.zip"
EXPECTED_JURISDICTION = "CN"
EXPECTED_QUERY_SCOPE = "control_and_system_metadata_only"
EXPECTED_OPERATOR_COMMENT_SHA256 = (
    "499a8ea46e08aa2a95dacf80e2e516099ced59e9dde6be9fdabad4f8cf8a8877"
)
OPERATOR_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "evidence"
    / "release"
    / "cn_m16_prior_runtime_operator_acceptance.json"
)
ACCEPTED_SERVING_STATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "evidence"
    / "release"
    / "cn_m16_lightweight_serving_checkpoint.json"
)


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def load_operator_runtime_evidence(
    path: str | Path = OPERATOR_EVIDENCE_PATH,
) -> dict[str, Any]:
    return _load_json_object(path, "operator runtime evidence")


def load_serving_state_evidence(path: str | Path) -> dict[str, Any]:
    return _load_json_object(path, "CN serving-state evidence")


def _canonical_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reason(code: str, message: str, *, severity: str = "BLOCKED") -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def validate_operator_runtime_evidence(
    evidence: dict[str, Any],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []

    required_values = {
        "schema_version": OPERATOR_EVIDENCE_SCHEMA_VERSION,
        "jurisdiction": EXPECTED_JURISDICTION,
        "source_package_file_name": EXPECTED_SOURCE_PACKAGE,
        "evidence_type": EXPECTED_OPERATOR_EVIDENCE_TYPE,
        "operator_decision": EXPECTED_OPERATOR_DECISION,
    }
    for field, expected in required_values.items():
        actual = evidence.get(field)
        if actual != expected:
            reasons.append(
                _reason(
                    "OPERATOR_EVIDENCE_FIELD_MISMATCH",
                    f"{field} must be {expected!r}; found {actual!r}.",
                )
            )

    boundary_checks = (
        (
            evidence.get("requires_current_serving_state") is True,
            "CURRENT_SERVING_STATE_NOT_REQUIRED",
            "Operator evidence must require a current serving-state checkpoint.",
        ),
        (
            evidence.get("fresh_full_corpus_validation_claimed") is False,
            "FRESH_FULL_CORPUS_VALIDATION_OVERCLAIMED",
            "Prior operator evidence must not claim a fresh full-corpus validation.",
        ),
        (
            evidence.get("package_replay_or_rescan_required") is False,
            "PACKAGE_RERUN_POLICY_DRIFT",
            "Accepted prior evidence must not require package replay or rescan.",
        ),
        (
            evidence.get("target_host_runtime_details_reconstructed") is False,
            "TARGET_RUNTIME_DETAILS_RECONSTRUCTED",
            "Promotion evidence must not invent missing target-host runtime details.",
        ),
    )
    for passed, code, message in boundary_checks:
        if not passed:
            reasons.append(_reason(code, message))

    source = evidence.get("source_reference") or {}
    expected_source = {
        "kind": "GITHUB_ISSUE_COMMENT",
        "repository": "yoomarks/markorbit-data-engine",
        "issue_number": 247,
        "comment_id": 5421693252,
        "comment_url": (
            "https://github.com/yoomarks/markorbit-data-engine/issues/247"
            "#issuecomment-5421693252"
        ),
        "comment_body_sha256": EXPECTED_OPERATOR_COMMENT_SHA256,
    }
    for field, expected in expected_source.items():
        if source.get(field) != expected:
            reasons.append(
                _reason(
                    "OPERATOR_EVIDENCE_SOURCE_REFERENCE_MISMATCH",
                    f"source_reference.{field} must be {expected!r}.",
                )
            )

    decision_text = str(evidence.get("source_operator_decision_text") or "")
    if _text_sha256(decision_text) != EXPECTED_OPERATOR_COMMENT_SHA256:
        reasons.append(
            _reason(
                "OPERATOR_EVIDENCE_SOURCE_TEXT_HASH_MISMATCH",
                "The recorded operator decision text does not match the pinned source hash.",
            )
        )

    return reasons


def validate_serving_state_evidence(
    serving_state: dict[str, Any],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    status = str(serving_state.get("status") or "UNKNOWN")

    checks = (
        (
            serving_state.get("checkpoint_version") == CN_SERVING_CHECKPOINT_VERSION,
            "SERVING_STATE_VERSION_MISMATCH",
            "Current serving state must use the M1.6 lightweight checkpoint contract.",
        ),
        (
            status in {"PASS", "WARN"},
            "SERVING_STATE_NOT_PASS",
            f"Current serving state is {status!r}; expected PASS or WARN.",
        ),
        (
            serving_state.get("read_only") is True,
            "SERVING_STATE_NOT_READ_ONLY",
            "Serving-state evidence must be read-only.",
        ),
        (
            serving_state.get("evidence_mode") == "LIGHTWEIGHT_SERVING_CHECKPOINT",
            "SERVING_STATE_MODE_MISMATCH",
            "Serving-state evidence mode must be LIGHTWEIGHT_SERVING_CHECKPOINT.",
        ),
        (
            serving_state.get("query_scope") == EXPECTED_QUERY_SCOPE,
            "SERVING_STATE_QUERY_SCOPE_MISMATCH",
            "Promotion requires control/system-metadata-only serving-state evidence.",
        ),
        (
            serving_state.get("expected_file_name") == EXPECTED_SOURCE_PACKAGE,
            "SERVING_STATE_PACKAGE_MISMATCH",
            f"Serving state must cover {EXPECTED_SOURCE_PACKAGE!r}.",
        ),
        (
            serving_state.get("expected_package_success") is True,
            "SERVING_STATE_PACKAGE_NOT_SUCCESS",
            "The expected CN package must be recorded SUCCESS.",
        ),
        (
            serving_state.get("processing_package_count") == 0
            and serving_state.get("quiescent") is True,
            "SERVING_STATE_NOT_QUIESCENT",
            "No CN source package may be PROCESSING during promotion evaluation.",
        ),
        (
            serving_state.get("core_tables_ready") is True,
            "SERVING_STATE_CORE_TABLES_NOT_READY",
            "Critical CN serving tables must be present with active parts.",
        ),
        (
            serving_state.get("goods_schema_exact") is True,
            "SERVING_STATE_GOODS_SCHEMA_NOT_EXACT",
            "Current serving state must pass the exact goods schema guard.",
        ),
        (
            serving_state.get("full_corpus_scan") is False,
            "SERVING_STATE_FULL_CORPUS_SCAN_BOUNDARY_INVALID",
            "Lightweight promotion evidence must not claim a full-corpus scan.",
        ),
        (
            serving_state.get("package_reprocessed") is False,
            "SERVING_STATE_PACKAGE_REPROCESS_BOUNDARY_INVALID",
            "Lightweight promotion evidence must not reprocess the source package.",
        ),
        (
            serving_state.get("full_corpus_semantic_acceptance_claimed") is False,
            "SERVING_STATE_SEMANTIC_ACCEPTANCE_BOUNDARY_INVALID",
            "Lightweight evidence must not claim fresh full-corpus semantic acceptance.",
        ),
    )
    for passed, code, message in checks:
        if not passed:
            reasons.append(_reason(code, message))

    critical_tables = serving_state.get("critical_tables") or {}
    for table in CN_CRITICAL_TABLES:
        state = critical_tables.get(table) or {}
        if state.get("exists") is not True or int(state.get("active_parts") or 0) <= 0:
            reasons.append(
                _reason(
                    "SERVING_STATE_CRITICAL_TABLE_BLOCKED",
                    f"Critical table {table!r} is missing or has no active parts.",
                )
            )

    disks = serving_state.get("disks") or []
    if not disks:
        reasons.append(
            _reason(
                "SERVING_STATE_DISK_METADATA_MISSING",
                "Current serving state must include ClickHouse disk metadata.",
            )
        )

    return reasons


def evaluate_m17_release_promotion(
    *,
    operator_evidence: dict[str, Any],
    serving_state: dict[str, Any] | None,
) -> dict[str, Any]:
    operator_reasons = validate_operator_runtime_evidence(operator_evidence)
    serving_reasons: list[dict[str, str]] = []
    if serving_state is None:
        serving_reasons.append(
            _reason(
                "CURRENT_SERVING_STATE_EVIDENCE_REQUIRED",
                "Run the lightweight CN serving-state checkpoint and preserve its JSON report.",
            )
        )
    else:
        serving_reasons.extend(validate_serving_state_evidence(serving_state))

    reasons = operator_reasons + serving_reasons
    operator_valid = not operator_reasons
    serving_valid = serving_state is not None and not serving_reasons
    allowed = operator_valid and serving_valid
    serving_status = str(serving_state.get("status") or "UNKNOWN") if serving_state else None

    if allowed and serving_status == "WARN":
        status = "READY_FOR_M1_7_WITH_WARNINGS"
    elif allowed:
        status = "READY_FOR_M1_7"
    elif operator_valid and serving_state is None:
        status = "PENDING_CURRENT_SERVING_STATE"
    else:
        status = "BLOCKED"

    return {
        "contract_version": PROMOTION_CONTRACT_VERSION,
        "status": status,
        "read_only": True,
        "release_promotion_allowed": allowed,
        "promotion_target": "M1.7",
        "promotion_basis": (
            "PRIOR_RUNTIME_OPERATOR_ACCEPTED_PLUS_CURRENT_LIGHTWEIGHT_SERVING_STATE"
        ),
        "operator_evidence_valid": operator_valid,
        "operator_evidence_type": operator_evidence.get("evidence_type"),
        "operator_evidence_id": operator_evidence.get("evidence_id"),
        "operator_evidence_sha256": _canonical_sha256(operator_evidence),
        "operator_source_comment_body_sha256": EXPECTED_OPERATOR_COMMENT_SHA256,
        "current_serving_state_present": serving_state is not None,
        "current_serving_state_valid": serving_valid,
        "current_serving_state_status": serving_status,
        "current_serving_state_checkpoint_version": (
            serving_state.get("checkpoint_version") if serving_state else None
        ),
        "current_serving_state_sha256": (
            _canonical_sha256(serving_state) if serving_state else None
        ),
        "source_package_file_name": EXPECTED_SOURCE_PACKAGE,
        "fresh_full_corpus_validation_claimed": False,
        "full_corpus_semantic_acceptance_claimed": False,
        "package_replay_or_rescan_required": False,
        "reasons": reasons,
    }


def build_m17_release_promotion_contract(
    serving_state: dict[str, Any] | None = None,
    *,
    operator_evidence_path: str | Path = OPERATOR_EVIDENCE_PATH,
    accepted_serving_state_path: str | Path = ACCEPTED_SERVING_STATE_PATH,
) -> dict[str, Any]:
    operator_evidence = load_operator_runtime_evidence(operator_evidence_path)
    if serving_state is None:
        accepted_path = Path(accepted_serving_state_path)
        if accepted_path.is_file():
            serving_state = load_serving_state_evidence(accepted_path)
    return evaluate_m17_release_promotion(
        operator_evidence=operator_evidence,
        serving_state=serving_state,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate M1.7 promotion from operator-accepted prior CN runtime evidence."
    )
    parser.add_argument(
        "--serving-state-report",
        type=Path,
        help="JSON report produced by app.cn.serving_state_checkpoint.",
    )
    parser.add_argument(
        "--operator-evidence",
        type=Path,
        default=OPERATOR_EVIDENCE_PATH,
        help="Pinned operator prior-runtime evidence JSON.",
    )
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    serving_state = (
        load_serving_state_evidence(args.serving_state_report)
        if args.serving_state_report
        else None
    )
    report = build_m17_release_promotion_contract(
        serving_state,
        operator_evidence_path=args.operator_evidence,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=not args.compact,
        )
    )
    if args.require_ready and not report["release_promotion_allowed"]:
        return 4
    return 0 if report["status"] != "BLOCKED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
