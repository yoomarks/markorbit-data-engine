from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from app.platformization_checkpoint import build_platformization_checkpoint


PLATFORMIZATION_RUNTIME_GATE_VERSION = "MARKORBIT_PLATFORMIZATION_RUNTIME_GATE_V3"
_REQUIRED_STATIC_CHECKPOINT = "MARKORBIT_PLATFORMIZATION_CHECKPOINT_V1"
_REQUIRED_CN_ACCEPTANCE_RECEIPT = "CN_M16_POST_IMPORT_ACCEPTANCE_V1"
_REQUIRED_CN_CHECKPOINT = "CN_M16_FINAL_CHECKPOINT_V1"
_REQUIRED_CN_SERVING_CHECKPOINT = "CN_M16_LIGHTWEIGHT_SERVING_CHECKPOINT_V1"
_DEFAULT_EXPECTED_CN_FILE_NAME = "2023_5.zip"
_PASS_STATUSES = {"PASS", "PASS_WITH_WARNINGS"}
_LIGHTWEIGHT_PASS_STATUSES = {"PASS", "WARN"}


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    evidence_path = Path(path)
    with evidence_path.open("r", encoding="utf-8-sig") as handle:
        evidence = json.load(handle)
    if not isinstance(evidence, dict):
        raise ValueError(f"{label} must be a JSON object")
    return evidence


def load_cn_acceptance_receipt(path: str | Path) -> dict[str, Any]:
    return _load_json_object(path, "CN acceptance receipt")


def load_cn_serving_checkpoint(path: str | Path) -> dict[str, Any]:
    return _load_json_object(path, "CN serving-state checkpoint")


def _validate_full_acceptance_receipt(
    *,
    receipt: dict[str, Any],
    expected_cn_file_name: str,
    reasons: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any] | None, str | None, str | None]:
    receipt_version = receipt.get("post_import_version")
    receipt_status = str(receipt.get("status") or "UNKNOWN")
    receipt_file_name = str(receipt.get("expected_file_name") or "")
    next_action = receipt.get("next_action") or {}
    next_mode = str(next_action.get("mode") or "") if isinstance(next_action, dict) else ""

    if receipt_version != _REQUIRED_CN_ACCEPTANCE_RECEIPT:
        reasons.append(
            {
                "code": "CN_ACCEPTANCE_RECEIPT_VERSION_MISMATCH",
                "expected": _REQUIRED_CN_ACCEPTANCE_RECEIPT,
                "actual": receipt_version,
            }
        )
    if receipt.get("read_only") is not True:
        reasons.append({"code": "CN_ACCEPTANCE_RECEIPT_NOT_READ_ONLY"})
    if receipt_file_name != expected_cn_file_name:
        reasons.append(
            {
                "code": "CN_ACCEPTANCE_RECEIPT_FILE_MISMATCH",
                "expected": expected_cn_file_name,
                "actual": receipt_file_name,
            }
        )
    if receipt.get("expected_package_success") is not True:
        reasons.append({"code": "CN_ACCEPTANCE_EXPECTED_PACKAGE_NOT_SUCCESS"})
    if str(receipt.get("readiness_status") or "") != "COMPLETE":
        reasons.append(
            {
                "code": "CN_ACCEPTANCE_REPLAY_NOT_COMPLETE",
                "actual": receipt.get("readiness_status"),
            }
        )
    if receipt.get("final_checkpoint_executed") is not True:
        reasons.append({"code": "CN_ACCEPTANCE_FINAL_CHECKPOINT_NOT_EXECUTED"})
    if receipt_status not in _PASS_STATUSES:
        reasons.append(
            {
                "code": "CN_ACCEPTANCE_RECEIPT_STATUS_NOT_PASS",
                "status": receipt_status,
                "reasons": receipt.get("reasons") or [],
            }
        )
    if next_mode != "CN_REPLAY_ACCEPTED":
        reasons.append(
            {
                "code": "CN_ACCEPTANCE_MARKER_MISSING",
                "expected": "CN_REPLAY_ACCEPTED",
                "actual": next_mode,
            }
        )

    candidate_checkpoint = receipt.get("final_checkpoint")
    cn_checkpoint = candidate_checkpoint if isinstance(candidate_checkpoint, dict) else None
    cn_status = str(cn_checkpoint.get("status") or "UNKNOWN") if cn_checkpoint else None
    if cn_checkpoint is None:
        reasons.append({"code": "CN_ACCEPTANCE_FINAL_CHECKPOINT_MISSING"})
    else:
        if cn_checkpoint.get("checkpoint_version") != _REQUIRED_CN_CHECKPOINT:
            reasons.append(
                {
                    "code": "CN_RUNTIME_CHECKPOINT_VERSION_MISMATCH",
                    "expected": _REQUIRED_CN_CHECKPOINT,
                    "actual": cn_checkpoint.get("checkpoint_version"),
                }
            )
        if cn_checkpoint.get("read_only") is not True:
            reasons.append({"code": "CN_RUNTIME_CHECKPOINT_NOT_READ_ONLY"})
        if cn_checkpoint.get("acceptance_executed") is not True:
            reasons.append({"code": "CN_RUNTIME_ACCEPTANCE_NOT_EXECUTED"})
        if cn_checkpoint.get("ready_for_next_domain") is not True:
            reasons.append(
                {
                    "code": "CN_RUNTIME_NOT_ACCEPTED",
                    "status": cn_status,
                    "reasons": cn_checkpoint.get("reasons") or [],
                }
            )
        if cn_status not in _PASS_STATUSES:
            reasons.append({"code": "CN_RUNTIME_STATUS_NOT_PASS", "status": cn_status})

    passed = bool(
        receipt_version == _REQUIRED_CN_ACCEPTANCE_RECEIPT
        and receipt.get("read_only") is True
        and receipt_file_name == expected_cn_file_name
        and receipt.get("expected_package_success") is True
        and str(receipt.get("readiness_status") or "") == "COMPLETE"
        and receipt.get("final_checkpoint_executed") is True
        and receipt_status in _PASS_STATUSES
        and next_mode == "CN_REPLAY_ACCEPTED"
        and cn_checkpoint is not None
        and cn_checkpoint.get("checkpoint_version") == _REQUIRED_CN_CHECKPOINT
        and cn_checkpoint.get("read_only") is True
        and cn_checkpoint.get("acceptance_executed") is True
        and cn_checkpoint.get("ready_for_next_domain") is True
        and cn_status in _PASS_STATUSES
    )
    return passed, cn_checkpoint, cn_status, next_mode


def _validate_lightweight_serving_checkpoint(
    *,
    checkpoint: dict[str, Any],
    expected_cn_file_name: str,
    reasons: list[dict[str, Any]],
) -> bool:
    status = str(checkpoint.get("status") or "UNKNOWN")
    file_name = str(checkpoint.get("expected_file_name") or "")

    checks = (
        (
            checkpoint.get("checkpoint_version") == _REQUIRED_CN_SERVING_CHECKPOINT,
            {
                "code": "CN_SERVING_CHECKPOINT_VERSION_MISMATCH",
                "expected": _REQUIRED_CN_SERVING_CHECKPOINT,
                "actual": checkpoint.get("checkpoint_version"),
            },
        ),
        (
            checkpoint.get("read_only") is True,
            {"code": "CN_SERVING_CHECKPOINT_NOT_READ_ONLY"},
        ),
        (
            checkpoint.get("evidence_mode") == "LIGHTWEIGHT_SERVING_CHECKPOINT",
            {"code": "CN_SERVING_CHECKPOINT_MODE_INVALID"},
        ),
        (
            file_name == expected_cn_file_name,
            {
                "code": "CN_SERVING_CHECKPOINT_FILE_MISMATCH",
                "expected": expected_cn_file_name,
                "actual": file_name,
            },
        ),
        (
            checkpoint.get("expected_package_success") is True,
            {"code": "CN_SERVING_EXPECTED_PACKAGE_NOT_SUCCESS"},
        ),
        (
            checkpoint.get("processing_package_count") == 0
            and checkpoint.get("quiescent") is True,
            {"code": "CN_SERVING_STATE_NOT_QUIESCENT"},
        ),
        (
            checkpoint.get("core_tables_ready") is True,
            {"code": "CN_SERVING_CORE_TABLES_NOT_READY"},
        ),
        (
            checkpoint.get("goods_schema_exact") is True,
            {"code": "CN_SERVING_GOODS_SCHEMA_NOT_EXACT"},
        ),
        (
            checkpoint.get("query_scope") == "control_and_system_metadata_only",
            {"code": "CN_SERVING_QUERY_SCOPE_INVALID"},
        ),
        (
            checkpoint.get("full_corpus_scan") is False,
            {"code": "CN_SERVING_FULL_CORPUS_SCAN_BOUNDARY_INVALID"},
        ),
        (
            checkpoint.get("package_reprocessed") is False,
            {"code": "CN_SERVING_PACKAGE_REPROCESS_BOUNDARY_INVALID"},
        ),
        (
            checkpoint.get("full_corpus_semantic_acceptance_claimed") is False,
            {"code": "CN_SERVING_SEMANTIC_ACCEPTANCE_BOUNDARY_INVALID"},
        ),
        (
            status in _LIGHTWEIGHT_PASS_STATUSES,
            {
                "code": "CN_SERVING_CHECKPOINT_STATUS_NOT_PASS",
                "status": status,
                "reasons": checkpoint.get("reasons") or [],
            },
        ),
    )
    for passed, reason in checks:
        if not passed:
            reasons.append(reason)
    return all(passed for passed, _reason_item in checks)


def evaluate_platformization_runtime_gate(
    *,
    static_checkpoint: dict[str, Any],
    cn_acceptance_receipt: dict[str, Any] | None = None,
    cn_serving_checkpoint: dict[str, Any] | None = None,
    expected_cn_file_name: str = _DEFAULT_EXPECTED_CN_FILE_NAME,
    receipt_error: dict[str, Any] | None = None,
    serving_checkpoint_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine static M1.7 readiness with one persisted target-host CN evidence mode.

    Legacy full-acceptance receipts remain valid. The lightweight mode verifies only
    control-plane/serving-state continuity after prior operator-accepted validation;
    it is intentionally required to state that it did not scan or semantically
    re-accept the full corpus.
    """

    reasons: list[dict[str, Any]] = []
    static_ready = (
        static_checkpoint.get("version") == _REQUIRED_STATIC_CHECKPOINT
        and static_checkpoint.get("code_ready") is True
        and static_checkpoint.get("runtime_acceptance_evaluated") is False
        and static_checkpoint.get("release_promotion_allowed") is False
    )
    if not static_ready:
        reasons.append(
            {
                "code": "PLATFORMIZATION_STATIC_CHECKPOINT_NOT_READY",
                "static_version": static_checkpoint.get("version"),
                "static_status": static_checkpoint.get("status"),
                "static_reasons": static_checkpoint.get("reasons") or [],
            }
        )

    receipt_loaded = cn_acceptance_receipt is not None
    serving_loaded = cn_serving_checkpoint is not None
    evidence_mode = "NONE"
    runtime_passed = False
    cn_checkpoint: dict[str, Any] | None = None
    cn_status: str | None = None
    next_mode: str | None = None
    evidence_status: str | None = None
    evidence_version: str | None = None

    if static_ready:
        supplied_count = int(receipt_loaded or receipt_error is not None) + int(
            serving_loaded or serving_checkpoint_error is not None
        )
        if supplied_count > 1:
            reasons.append({"code": "CN_RUNTIME_EVIDENCE_AMBIGUOUS"})
        elif receipt_error is not None:
            reasons.append(receipt_error)
        elif serving_checkpoint_error is not None:
            reasons.append(serving_checkpoint_error)
        elif receipt_loaded:
            evidence_mode = "FULL_ACCEPTANCE_RECEIPT"
            evidence_status = str(cn_acceptance_receipt.get("status") or "UNKNOWN")
            evidence_version = str(cn_acceptance_receipt.get("post_import_version") or "")
            runtime_passed, cn_checkpoint, cn_status, next_mode = (
                _validate_full_acceptance_receipt(
                    receipt=cn_acceptance_receipt,
                    expected_cn_file_name=expected_cn_file_name,
                    reasons=reasons,
                )
            )
        elif serving_loaded:
            evidence_mode = "LIGHTWEIGHT_SERVING_CHECKPOINT"
            evidence_status = str(cn_serving_checkpoint.get("status") or "UNKNOWN")
            evidence_version = str(cn_serving_checkpoint.get("checkpoint_version") or "")
            cn_status = evidence_status
            runtime_passed = _validate_lightweight_serving_checkpoint(
                checkpoint=cn_serving_checkpoint,
                expected_cn_file_name=expected_cn_file_name,
                reasons=reasons,
            )
        else:
            reasons.append({"code": "CN_ACCEPTANCE_RECEIPT_MISSING"})

    runtime_evaluated = bool(
        static_ready
        and evidence_mode != "NONE"
        and receipt_error is None
        and serving_checkpoint_error is None
    )
    promotion_eligible = static_ready and runtime_passed and not reasons
    if not promotion_eligible:
        status = "BLOCKED"
    elif evidence_status in {"PASS_WITH_WARNINGS", "WARN"}:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"

    if evidence_mode == "FULL_ACCEPTANCE_RECEIPT":
        acceptance_source = _REQUIRED_CN_ACCEPTANCE_RECEIPT
        promotion_basis = "PERSISTED_FULL_ACCEPTANCE_RECEIPT"
    elif evidence_mode == "LIGHTWEIGHT_SERVING_CHECKPOINT":
        acceptance_source = _REQUIRED_CN_SERVING_CHECKPOINT
        promotion_basis = "PERSISTED_LIGHTWEIGHT_SERVING_STATE_AFTER_PRIOR_VALIDATION"
    else:
        acceptance_source = None
        promotion_basis = None

    return {
        "version": PLATFORMIZATION_RUNTIME_GATE_VERSION,
        "status": status,
        "read_only": True,
        "receipt_driven": evidence_mode == "FULL_ACCEPTANCE_RECEIPT",
        "runtime_evidence_mode": evidence_mode,
        "runtime_evidence_version": evidence_version,
        "runtime_evidence_status": evidence_status,
        "static_code_ready": static_ready,
        "runtime_acceptance_required": True,
        "runtime_acceptance_evaluated": runtime_evaluated,
        "runtime_acceptance_passed": runtime_passed,
        "cn_acceptance_receipt_loaded": receipt_loaded,
        "cn_acceptance_receipt_version": (
            cn_acceptance_receipt.get("post_import_version") if receipt_loaded else None
        ),
        "cn_acceptance_receipt_status": (
            cn_acceptance_receipt.get("status") if receipt_loaded else None
        ),
        "cn_acceptance_expected_file_name": expected_cn_file_name,
        "cn_acceptance_receipt_file_name": (
            cn_acceptance_receipt.get("expected_file_name") if receipt_loaded else None
        ),
        "cn_acceptance_next_mode": next_mode,
        "cn_serving_checkpoint_loaded": serving_loaded,
        "cn_serving_checkpoint_version": (
            cn_serving_checkpoint.get("checkpoint_version") if serving_loaded else None
        ),
        "cn_serving_checkpoint_status": (
            cn_serving_checkpoint.get("status") if serving_loaded else None
        ),
        "cn_runtime_checkpoint_version": (
            cn_checkpoint.get("checkpoint_version") if cn_checkpoint else None
        ),
        "cn_runtime_status": cn_status,
        "release_promotion_eligible": promotion_eligible,
        "release_promoted": False,
        "release_promotion_action": "SEPARATE_EXPLICIT_CHANGE_ONLY_AFTER_GATE_PASS",
        "promotion_basis": promotion_basis,
        "real_cn_runtime_accepted": promotion_eligible,
        "real_cn_runtime_acceptance_source": (
            acceptance_source if promotion_eligible else None
        ),
        "reasons": reasons,
        "static_checkpoint": static_checkpoint,
        "cn_acceptance_receipt": cn_acceptance_receipt,
        "cn_serving_checkpoint": cn_serving_checkpoint,
        "cn_checkpoint": cn_checkpoint,
    }


def build_platformization_runtime_gate(
    *,
    cn_acceptance_receipt_path: str | Path | None = None,
    cn_serving_checkpoint_path: str | Path | None = None,
    expected_cn_file_name: str = _DEFAULT_EXPECTED_CN_FILE_NAME,
    static_builder: Callable[[], dict[str, Any]] = build_platformization_checkpoint,
    receipt_loader: Callable[[str | Path], dict[str, Any]] = load_cn_acceptance_receipt,
    serving_checkpoint_loader: Callable[[str | Path], dict[str, Any]] = load_cn_serving_checkpoint,
) -> dict[str, Any]:
    static_checkpoint = static_builder()
    if not static_checkpoint.get("code_ready"):
        return evaluate_platformization_runtime_gate(static_checkpoint=static_checkpoint)

    receipt_path_supplied = bool(
        cn_acceptance_receipt_path is not None
        and str(cn_acceptance_receipt_path).strip()
    )
    serving_path_supplied = bool(
        cn_serving_checkpoint_path is not None
        and str(cn_serving_checkpoint_path).strip()
    )
    if receipt_path_supplied and serving_path_supplied:
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            expected_cn_file_name=expected_cn_file_name,
            receipt_error={"code": "CN_RUNTIME_EVIDENCE_AMBIGUOUS"},
            serving_checkpoint_error={"code": "CN_RUNTIME_EVIDENCE_AMBIGUOUS"},
        )
    if not receipt_path_supplied and not serving_path_supplied:
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            expected_cn_file_name=expected_cn_file_name,
        )

    if receipt_path_supplied:
        try:
            receipt = receipt_loader(cn_acceptance_receipt_path)  # type: ignore[arg-type]
        except FileNotFoundError:
            return evaluate_platformization_runtime_gate(
                static_checkpoint=static_checkpoint,
                expected_cn_file_name=expected_cn_file_name,
                receipt_error={"code": "CN_ACCEPTANCE_RECEIPT_NOT_FOUND"},
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
            return evaluate_platformization_runtime_gate(
                static_checkpoint=static_checkpoint,
                expected_cn_file_name=expected_cn_file_name,
                receipt_error={
                    "code": "CN_ACCEPTANCE_RECEIPT_INVALID",
                    "error_type": type(exc).__name__,
                },
            )
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            cn_acceptance_receipt=receipt,
            expected_cn_file_name=expected_cn_file_name,
        )

    try:
        checkpoint = serving_checkpoint_loader(cn_serving_checkpoint_path)  # type: ignore[arg-type]
    except FileNotFoundError:
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            expected_cn_file_name=expected_cn_file_name,
            serving_checkpoint_error={"code": "CN_SERVING_CHECKPOINT_NOT_FOUND"},
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            expected_cn_file_name=expected_cn_file_name,
            serving_checkpoint_error={
                "code": "CN_SERVING_CHECKPOINT_INVALID",
                "error_type": type(exc).__name__,
            },
        )
    return evaluate_platformization_runtime_gate(
        static_checkpoint=static_checkpoint,
        cn_serving_checkpoint=checkpoint,
        expected_cn_file_name=expected_cn_file_name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persisted-evidence M1.7 code + real CN runtime gate"
    )
    evidence_group = parser.add_mutually_exclusive_group()
    evidence_group.add_argument(
        "--cn-acceptance-receipt",
        help=(
            "Legacy persisted full CN acceptance report. No CN runtime query is "
            "executed by this gate."
        ),
    )
    evidence_group.add_argument(
        "--cn-serving-checkpoint",
        help=(
            "Persisted lightweight CN serving-state checkpoint. This mode consumes "
            "metadata/control evidence only and does not claim a new full-corpus audit."
        ),
    )
    parser.add_argument(
        "--expected-cn-file-name",
        default=_DEFAULT_EXPECTED_CN_FILE_NAME,
        help="Expected CN package identity recorded in the persisted evidence.",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path=args.cn_acceptance_receipt,
        cn_serving_checkpoint_path=args.cn_serving_checkpoint,
        expected_cn_file_name=args.expected_cn_file_name,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            default=str,
        )
    )
    return 0 if report["release_promotion_eligible"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
