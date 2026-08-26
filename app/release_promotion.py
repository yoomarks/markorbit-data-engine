from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.cn.serving_state_checkpoint import (
    CHECKPOINT_VERSION as CN_SERVING_STATE_CHECKPOINT_VERSION,
)
from app.cn.serving_state_checkpoint import CRITICAL_TABLES as CN_CRITICAL_TABLES
from app.cn.serving_state_checkpoint import DISK_WARN_FREE_RATIO


PROMOTION_CONTRACT_VERSION = "MARKORBIT_M17_RELEASE_PROMOTION_V1"
OPERATOR_EVIDENCE_SCHEMA_VERSION = "CN_OPERATOR_RUNTIME_EVIDENCE_V1"
EXPECTED_OPERATOR_EVIDENCE_TYPE = "PRIOR_RUNTIME_VALIDATION_OPERATOR_ACCEPTED"
EXPECTED_OPERATOR_DECISION = "DO_NOT_RERUN_EXPENSIVE_PACKAGE_VALIDATION"
EXPECTED_SOURCE_PACKAGE = "2023_5.zip"
EXPECTED_JURISDICTION = "CN"
EXPECTED_QUERY_SCOPE = "control_and_system_metadata_only"
OPERATOR_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "evidence"
    / "release"
    / "cn_m16_prior_runtime_operator_acceptance.json"
)


def load_operator_runtime_evidence(
    path: Path = OPERATOR_EVIDENCE_PATH,
) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reason(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


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

    if evidence.get("requires_current_serving_state") is not True:
        reasons.append(
            _reason(
                "CURRENT_SERVING_STATE_NOT_REQUIRED",
                "Operator evidence must require a current serving-state checkpoint.",
            )
        )
    if evidence.get("fresh_full_corpus_validation_claimed") is not False:
        reasons.append(
            _reason(
                "FRESH_FULL_CORPUS_VALIDATION_OVERCLAIMED",
                "Prior operator evidence must not claim a new full-corpus validation.",
            )
        )
    if evidence.get("package_replay_or_rescan_required") is not False:
        reasons.append(
            _reason(
                "PACKAGE_RERUN_POLICY_DRIFT",
                "Accepted prior evidence must not require package replay or rescan.",
            )
        )
    if evidence.get("target_host_runtime_details_reconstructed") is not False:
        reasons.append(
            _reason(
                "TARGET_RUNTIME_DETAILS_RECONSTRUCTED",
                "Promotion evidence must not invent missing target-host runtime details.",
            )
        )

    source = evidence.get("source_reference") or {}
    if source.get("repository") != "yoomarks/markorbit-data-engine":
        reasons.append(
            _reason(
                "OPERATOR_EVIDENCE_SOURCE_REPOSITORY_MISMATCH",
                "Operator evidence must reference the Data Engine repository.",
            )
        )
    if source.get("issue_number") != 247 or source.get("comment_id") != 5421693252:
        reasons.append(
            _reason(
                "OPERATOR_EVIDENCE_SOURCE_REFERENCE_MISMATCH",
                "Operator evidence must reference issue #247 comment 5421693252.",
            )
        )

    return reasons


def _validate_serving_state(
    serving_state: dict[str, Any],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []

    if serving_state.get("checkpoint_version") != CN_SERVING_STATE_CHECKPOINT_VERSION:
        reasons.append(
            _reason(
                "SERVING_STATE_VERSION_MISMATCH",
                "Current serving state must use the lightweight CN checkpoint contract.",
            )
        )
    if serving_state.get("status") != "PASS":
        reasons.append(
            _reason(
                "SERVING_STATE_NOT_PASS",
                f"Current serving state is {serving_state.get('status')!r}, not 'PASS'.",
            )
        )
    if serving_state.get("read_only") is not True:
        reasons.append(
            _reason(
                "SERVING_STATE_NOT_READ_ONLY",
                "Current serving-state evidence must be produced by the read-only gate.",
            )
        )
    if serving_state.get("query_scope") != EXPECTED_QUERY_SCOPE:
        reasons.append(
            _reason(
                "SERVING_STATE_QUERY_SCOPE_MISMATCH",
                "Promotion requires the control/system-metadata-only checkpoint scope.",
            )
        )
    if serving_state.get("expected_file_name") != EXPECTED_SOURCE_PACKAGE:
        reasons.append(
            _reason(
                "SERVING_STATE_PACKAGE_MISMATCH",
                f"Serving state must cover {EXPECTED_SOURCE_PACKAGE!r}.",
            )
        )

    expected_package = serving_state.get("expected_package") or {}
    if expected_package.get("file_name") != EXPECTED_SOURCE_PACKAGE:
        reasons.append(
            _reason(
                "SERVING_STATE_PACKAGE_RECORD_MISMATCH",
                "The successful package record must match the expected CN package.",
            )
        )
    if expected_package.get("status") != "SUCCESS":
        reasons.append(
            _reason(
                "SERVING_STATE_PACKAGE_NOT_SUCCESS",
                "Current serving state must report the expected package as SUCCESS.",
            )
        )
    if int(serving_state.get("processing_package_count") or 0) != 0:
        reasons.append(
            _reason(
                "SERVING_STATE_PACKAGE_PROCESSING",
                "No CN source package may be PROCESSING during promotion evaluation.",
            )
        )
    if serving_state.get("goods_schema_exact") is not True:
        reasons.append(
            _reason(
                "SERVING_STATE_GOODS_SCHEMA_NOT_EXACT",
                "Current serving state must pass the exact goods schema guard.",
            )
        )

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
    for disk in disks:
        free_ratio = disk.get("free_ratio")
        if free_ratio is None:
            reasons.append(
                _reason(
                    "SERVING_STATE_DISK_CAPACITY_UNKNOWN",
                    f"ClickHouse disk {disk.get('name')!r} has no usable free ratio.",
                )
            )
            continue
        if float(free_ratio) < DISK_WARN_FREE_RATIO:
            reasons.append(
                _reason(
                    "SERVING_STATE_DISK_HEADROOM_BELOW_PROMOTION_THRESHOLD",
                    f"ClickHouse disk {disk.get('name')!r} has only "
                    f"{float(free_ratio):.1%} free space; promotion requires "
                    f"at least {DISK_WARN_FREE_RATIO:.0%}.",
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
        serving_reasons.extend(_validate_serving_state(serving_state))

    reasons = operator_reasons + serving_reasons
    operator_evidence_valid = not operator_reasons
    current_serving_state_valid = serving_state is not None and not serving_reasons
    release_promotion_allowed = operator_evidence_valid and current_serving_state_valid

    if release_promotion_allowed:
        status = "READY_FOR_M1_7"
    elif operator_evidence_valid and serving_state is None:
        status = "PENDING_CURRENT_SERVING_STATE"
    else:
        status = "BLOCKED"

    return {
        "contract_version": PROMOTION_CONTRACT_VERSION,
        "status": status,
        "release_promotion_allowed": release_promotion_allowed,
        "operator_evidence_valid": operator_evidence_valid,
        "operator_evidence_type": operator_evidence.get("evidence_type"),
        "operator_evidence_id": operator_evidence.get("evidence_id"),
        "operator_evidence_sha256": _canonical_sha256(operator_evidence),
        "current_serving_state_present": serving_state is not None,
        "current_serving_state_valid": current_serving_state_valid,
        "current_serving_state_checkpoint_version": (
            serving_state.get("checkpoint_version") if serving_state else None
        ),
        "source_package_file_name": EXPECTED_SOURCE_PACKAGE,
        "fresh_full_corpus_validation_claimed": False,
        "package_replay_or_rescan_required": False,
        "reasons": reasons,
    }


def build_m17_release_promotion_contract(
    serving_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return evaluate_m17_release_promotion(
        operator_evidence=load_operator_runtime_evidence(),
        serving_state=serving_state,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate M1.7 promotion from accepted prior runtime evidence."
    )
    parser.add_argument(
        "--serving-state-report",
        type=Path,
        help="JSON report produced by app.cn.serving_state_checkpoint.",
    )
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()

    serving_state = None
    if args.serving_state_report:
        serving_state = json.loads(args.serving_state_report.read_text(encoding="utf-8"))

    report = build_m17_release_promotion_contract(serving_state)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.require_ready and not report["release_promotion_allowed"]:
        return 4
    return 0 if report["status"] != "BLOCKED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
