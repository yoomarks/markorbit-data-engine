from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from app.platformization_checkpoint import build_platformization_checkpoint


PLATFORMIZATION_RUNTIME_GATE_VERSION = "MARKORBIT_PLATFORMIZATION_RUNTIME_GATE_V2"
_REQUIRED_STATIC_CHECKPOINT = "MARKORBIT_PLATFORMIZATION_CHECKPOINT_V1"
_REQUIRED_CN_ACCEPTANCE_RECEIPT = "CN_M16_POST_IMPORT_ACCEPTANCE_V1"
_REQUIRED_CN_CHECKPOINT = "CN_M16_FINAL_CHECKPOINT_V1"
_DEFAULT_EXPECTED_CN_FILE_NAME = "2023_5.zip"
_PASS_STATUSES = {"PASS", "PASS_WITH_WARNINGS"}


def load_cn_acceptance_receipt(path: str | Path) -> dict[str, Any]:
    receipt_path = Path(path)
    with receipt_path.open("r", encoding="utf-8-sig") as handle:
        receipt = json.load(handle)
    if not isinstance(receipt, dict):
        raise ValueError("CN acceptance receipt must be a JSON object")
    return receipt


def evaluate_platformization_runtime_gate(
    *,
    static_checkpoint: dict[str, Any],
    cn_acceptance_receipt: dict[str, Any] | None,
    expected_cn_file_name: str = _DEFAULT_EXPECTED_CN_FILE_NAME,
    receipt_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine static M1.7 readiness with persisted target-host CN evidence.

    The runtime gate never re-runs CN replay, readiness, or final-checkpoint queries.
    It only validates a persisted report emitted by ``check-cn-post-import.ps1``.
    Missing, malformed, stale, or non-accepted evidence fails closed.
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
    receipt_status = None
    receipt_version = None
    receipt_file_name = None
    next_mode = None
    cn_checkpoint: dict[str, Any] | None = None
    cn_status = None
    runtime_passed = False

    if static_ready:
        if receipt_error is not None:
            reasons.append(receipt_error)
        elif cn_acceptance_receipt is None:
            reasons.append({"code": "CN_ACCEPTANCE_RECEIPT_MISSING"})
        else:
            receipt_version = cn_acceptance_receipt.get("post_import_version")
            receipt_status = str(cn_acceptance_receipt.get("status") or "UNKNOWN")
            receipt_file_name = str(
                cn_acceptance_receipt.get("expected_file_name") or ""
            )
            next_action = cn_acceptance_receipt.get("next_action") or {}
            if isinstance(next_action, dict):
                next_mode = str(next_action.get("mode") or "")

            if receipt_version != _REQUIRED_CN_ACCEPTANCE_RECEIPT:
                reasons.append(
                    {
                        "code": "CN_ACCEPTANCE_RECEIPT_VERSION_MISMATCH",
                        "expected": _REQUIRED_CN_ACCEPTANCE_RECEIPT,
                        "actual": receipt_version,
                    }
                )
            if cn_acceptance_receipt.get("read_only") is not True:
                reasons.append({"code": "CN_ACCEPTANCE_RECEIPT_NOT_READ_ONLY"})
            if receipt_file_name != expected_cn_file_name:
                reasons.append(
                    {
                        "code": "CN_ACCEPTANCE_RECEIPT_FILE_MISMATCH",
                        "expected": expected_cn_file_name,
                        "actual": receipt_file_name,
                    }
                )
            if cn_acceptance_receipt.get("expected_package_success") is not True:
                reasons.append({"code": "CN_ACCEPTANCE_EXPECTED_PACKAGE_NOT_SUCCESS"})
            if str(cn_acceptance_receipt.get("readiness_status") or "") != "COMPLETE":
                reasons.append(
                    {
                        "code": "CN_ACCEPTANCE_REPLAY_NOT_COMPLETE",
                        "actual": cn_acceptance_receipt.get("readiness_status"),
                    }
                )
            if cn_acceptance_receipt.get("final_checkpoint_executed") is not True:
                reasons.append({"code": "CN_ACCEPTANCE_FINAL_CHECKPOINT_NOT_EXECUTED"})
            if receipt_status not in _PASS_STATUSES:
                reasons.append(
                    {
                        "code": "CN_ACCEPTANCE_RECEIPT_STATUS_NOT_PASS",
                        "status": receipt_status,
                        "reasons": cn_acceptance_receipt.get("reasons") or [],
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

            candidate_checkpoint = cn_acceptance_receipt.get("final_checkpoint")
            if isinstance(candidate_checkpoint, dict):
                cn_checkpoint = candidate_checkpoint
                cn_status = str(cn_checkpoint.get("status") or "UNKNOWN")
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
                    reasons.append(
                        {
                            "code": "CN_RUNTIME_STATUS_NOT_PASS",
                            "status": cn_status,
                        }
                    )
            else:
                reasons.append({"code": "CN_ACCEPTANCE_FINAL_CHECKPOINT_MISSING"})

            runtime_passed = bool(
                receipt_version == _REQUIRED_CN_ACCEPTANCE_RECEIPT
                and cn_acceptance_receipt.get("read_only") is True
                and receipt_file_name == expected_cn_file_name
                and cn_acceptance_receipt.get("expected_package_success") is True
                and str(cn_acceptance_receipt.get("readiness_status") or "")
                == "COMPLETE"
                and cn_acceptance_receipt.get("final_checkpoint_executed") is True
                and receipt_status in _PASS_STATUSES
                and next_mode == "CN_REPLAY_ACCEPTED"
                and cn_checkpoint is not None
                and cn_checkpoint.get("checkpoint_version") == _REQUIRED_CN_CHECKPOINT
                and cn_checkpoint.get("read_only") is True
                and cn_checkpoint.get("acceptance_executed") is True
                and cn_checkpoint.get("ready_for_next_domain") is True
                and cn_status in _PASS_STATUSES
            )

    runtime_evaluated = static_ready and receipt_loaded and receipt_error is None
    if not static_ready:
        status = "BLOCKED"
    elif runtime_passed and not reasons:
        status = receipt_status or "PASS"
    else:
        status = "BLOCKED"

    promotion_eligible = static_ready and runtime_passed and not reasons
    return {
        "version": PLATFORMIZATION_RUNTIME_GATE_VERSION,
        "status": status,
        "read_only": True,
        "receipt_driven": True,
        "static_code_ready": static_ready,
        "runtime_acceptance_required": True,
        "runtime_acceptance_evaluated": runtime_evaluated,
        "runtime_acceptance_passed": runtime_passed,
        "cn_acceptance_receipt_loaded": receipt_loaded,
        "cn_acceptance_receipt_version": receipt_version,
        "cn_acceptance_receipt_status": receipt_status,
        "cn_acceptance_expected_file_name": expected_cn_file_name,
        "cn_acceptance_receipt_file_name": receipt_file_name,
        "cn_acceptance_next_mode": next_mode,
        "cn_runtime_checkpoint_version": (
            cn_checkpoint.get("checkpoint_version") if cn_checkpoint else None
        ),
        "cn_runtime_status": cn_status,
        "release_promotion_eligible": promotion_eligible,
        "release_promoted": False,
        "release_promotion_action": "SEPARATE_EXPLICIT_CHANGE_ONLY_AFTER_GATE_PASS",
        "real_cn_runtime_accepted": promotion_eligible,
        "real_cn_runtime_acceptance_source": (
            _REQUIRED_CN_ACCEPTANCE_RECEIPT if promotion_eligible else None
        ),
        "reasons": reasons,
        "static_checkpoint": static_checkpoint,
        "cn_acceptance_receipt": cn_acceptance_receipt,
        "cn_checkpoint": cn_checkpoint,
    }


def build_platformization_runtime_gate(
    *,
    cn_acceptance_receipt_path: str | Path | None = None,
    expected_cn_file_name: str = _DEFAULT_EXPECTED_CN_FILE_NAME,
    static_builder: Callable[[], dict[str, Any]] = build_platformization_checkpoint,
    receipt_loader: Callable[[str | Path], dict[str, Any]] = load_cn_acceptance_receipt,
) -> dict[str, Any]:
    static_checkpoint = static_builder()
    if not static_checkpoint.get("code_ready"):
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            cn_acceptance_receipt=None,
            expected_cn_file_name=expected_cn_file_name,
        )

    if cn_acceptance_receipt_path is None or not str(cn_acceptance_receipt_path).strip():
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            cn_acceptance_receipt=None,
            expected_cn_file_name=expected_cn_file_name,
        )

    try:
        receipt = receipt_loader(cn_acceptance_receipt_path)
    except FileNotFoundError:
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            cn_acceptance_receipt=None,
            expected_cn_file_name=expected_cn_file_name,
            receipt_error={"code": "CN_ACCEPTANCE_RECEIPT_NOT_FOUND"},
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            cn_acceptance_receipt=None,
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Receipt-driven M1.7 code + real CN runtime acceptance gate"
    )
    parser.add_argument(
        "--cn-acceptance-receipt",
        help=(
            "Persisted JSON report produced by scripts/check-cn-post-import.ps1. "
            "No CN runtime query is executed by this gate."
        ),
    )
    parser.add_argument(
        "--expected-cn-file-name",
        default=_DEFAULT_EXPECTED_CN_FILE_NAME,
        help="Expected CN package identity recorded in the persisted receipt.",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    report = build_platformization_runtime_gate(
        cn_acceptance_receipt_path=args.cn_acceptance_receipt,
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
