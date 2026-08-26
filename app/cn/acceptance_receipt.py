from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


RECEIPT_CHECK_VERSION = "CN_M16_ACCEPTANCE_RECEIPT_CHECK_V1"
_REQUIRED_RECEIPT_VERSION = "CN_M16_POST_IMPORT_ACCEPTANCE_V1"
_REQUIRED_CHECKPOINT_VERSION = "CN_M16_FINAL_CHECKPOINT_V1"
_PASS_STATUSES = {"PASS", "PASS_WITH_WARNINGS"}


def load_receipt(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("CN acceptance receipt must be a JSON object")
    return value


def validate_receipt(
    receipt: dict[str, Any], *, expected_file_name: str = "2023_5.zip"
) -> dict[str, Any]:
    reasons: list[dict[str, Any]] = []
    receipt_status = str(receipt.get("status") or "UNKNOWN")
    next_action = receipt.get("next_action") or {}
    next_mode = str(next_action.get("mode") or "") if isinstance(next_action, dict) else ""

    checks = (
        (
            receipt.get("post_import_version") == _REQUIRED_RECEIPT_VERSION,
            "RECEIPT_VERSION_MISMATCH",
            {"expected": _REQUIRED_RECEIPT_VERSION, "actual": receipt.get("post_import_version")},
        ),
        (receipt.get("read_only") is True, "RECEIPT_NOT_READ_ONLY", {}),
        (
            str(receipt.get("expected_file_name") or "") == expected_file_name,
            "RECEIPT_FILE_MISMATCH",
            {"expected": expected_file_name, "actual": receipt.get("expected_file_name")},
        ),
        (receipt.get("expected_package_success") is True, "EXPECTED_PACKAGE_NOT_SUCCESS", {}),
        (
            str(receipt.get("readiness_status") or "") == "COMPLETE",
            "REPLAY_NOT_COMPLETE",
            {"actual": receipt.get("readiness_status")},
        ),
        (receipt.get("final_checkpoint_executed") is True, "FINAL_CHECKPOINT_NOT_EXECUTED", {}),
        (
            receipt_status in _PASS_STATUSES,
            "RECEIPT_STATUS_NOT_PASS",
            {"actual": receipt_status},
        ),
        (
            next_mode == "CN_REPLAY_ACCEPTED",
            "ACCEPTANCE_MARKER_MISSING",
            {"expected": "CN_REPLAY_ACCEPTED", "actual": next_mode},
        ),
    )
    for passed, code, details in checks:
        if not passed:
            reasons.append({"code": code, **details})

    checkpoint = receipt.get("final_checkpoint")
    checkpoint_status = None
    if not isinstance(checkpoint, dict):
        reasons.append({"code": "FINAL_CHECKPOINT_MISSING"})
    else:
        checkpoint_status = str(checkpoint.get("status") or "UNKNOWN")
        checkpoint_checks = (
            (
                checkpoint.get("checkpoint_version") == _REQUIRED_CHECKPOINT_VERSION,
                "CHECKPOINT_VERSION_MISMATCH",
                {"expected": _REQUIRED_CHECKPOINT_VERSION, "actual": checkpoint.get("checkpoint_version")},
            ),
            (checkpoint.get("read_only") is True, "CHECKPOINT_NOT_READ_ONLY", {}),
            (checkpoint.get("acceptance_executed") is True, "CHECKPOINT_ACCEPTANCE_NOT_EXECUTED", {}),
            (checkpoint.get("ready_for_next_domain") is True, "CHECKPOINT_NOT_READY", {}),
            (
                checkpoint_status in _PASS_STATUSES,
                "CHECKPOINT_STATUS_NOT_PASS",
                {"actual": checkpoint_status},
            ),
        )
        for passed, code, details in checkpoint_checks:
            if not passed:
                reasons.append({"code": code, **details})

    accepted = not reasons
    return {
        "version": RECEIPT_CHECK_VERSION,
        "status": receipt_status if accepted else "BLOCKED",
        "read_only": True,
        "docker_required": False,
        "database_connection_required": False,
        "expected_file_name": expected_file_name,
        "receipt_file_name": receipt.get("expected_file_name"),
        "receipt_status": receipt_status,
        "readiness_status": receipt.get("readiness_status"),
        "final_checkpoint_executed": receipt.get("final_checkpoint_executed") is True,
        "checkpoint_status": checkpoint_status,
        "next_mode": next_mode,
        "accepted": accepted,
        "reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a persisted CN acceptance receipt without Docker or databases")
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--expected-file-name", default="2023_5.zip")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    try:
        receipt = load_receipt(args.receipt)
        report = validate_receipt(receipt, expected_file_name=args.expected_file_name)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
        report = {
            "version": RECEIPT_CHECK_VERSION,
            "status": "BLOCKED",
            "read_only": True,
            "docker_required": False,
            "database_connection_required": False,
            "expected_file_name": args.expected_file_name,
            "accepted": False,
            "reasons": [{"code": "RECEIPT_LOAD_FAILED", "error_type": type(exc).__name__}],
        }

    print(json.dumps(report, ensure_ascii=False, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2, sort_keys=True))
    return 0 if report.get("accepted") is True else 4


if __name__ == "__main__":
    raise SystemExit(main())
