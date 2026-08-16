from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from app.cn.final_checkpoint import build_final_checkpoint
from app.platformization_checkpoint import build_platformization_checkpoint


PLATFORMIZATION_RUNTIME_GATE_VERSION = "MARKORBIT_PLATFORMIZATION_RUNTIME_GATE_V1"
_REQUIRED_STATIC_CHECKPOINT = "MARKORBIT_PLATFORMIZATION_CHECKPOINT_V1"
_REQUIRED_CN_CHECKPOINT = "CN_M16_FINAL_CHECKPOINT_V1"
_PASS_STATUSES = {"PASS", "PASS_WITH_WARNINGS"}


def evaluate_platformization_runtime_gate(
    *,
    static_checkpoint: dict[str, Any],
    cn_checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    """Combine code readiness with the real read-only CN runtime checkpoint.

    This function never mutates databases or the repository release marker. A
    passing result means the current checkout is eligible for a separate release
    promotion decision; it does not perform that promotion.
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

    runtime_evaluated = cn_checkpoint is not None
    runtime_passed = False
    cn_status = None
    if cn_checkpoint is not None:
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
        runtime_passed = (
            cn_checkpoint.get("checkpoint_version") == _REQUIRED_CN_CHECKPOINT
            and cn_checkpoint.get("read_only") is True
            and cn_checkpoint.get("acceptance_executed") is True
            and cn_checkpoint.get("ready_for_next_domain") is True
            and cn_status in _PASS_STATUSES
        )

    if not static_ready:
        status = "BLOCKED"
    elif not runtime_evaluated:
        status = "RUNTIME_ACCEPTANCE_NOT_EVALUATED"
    elif runtime_passed and not reasons:
        status = cn_status or "PASS"
    else:
        status = "BLOCKED"

    promotion_eligible = static_ready and runtime_passed and not reasons
    return {
        "version": PLATFORMIZATION_RUNTIME_GATE_VERSION,
        "status": status,
        "read_only": True,
        "static_code_ready": static_ready,
        "runtime_acceptance_required": True,
        "runtime_acceptance_evaluated": runtime_evaluated,
        "runtime_acceptance_passed": runtime_passed,
        "cn_runtime_checkpoint_version": (
            cn_checkpoint.get("checkpoint_version") if cn_checkpoint else None
        ),
        "cn_runtime_status": cn_status,
        "release_promotion_eligible": promotion_eligible,
        "release_promoted": False,
        "release_promotion_action": "SEPARATE_EXPLICIT_CHANGE_ONLY_AFTER_GATE_PASS",
        "real_cn_runtime_accepted": promotion_eligible,
        "real_cn_runtime_acceptance_source": (
            _REQUIRED_CN_CHECKPOINT if promotion_eligible else None
        ),
        "reasons": reasons,
        "static_checkpoint": static_checkpoint,
        "cn_checkpoint": cn_checkpoint,
    }


def build_platformization_runtime_gate(
    *,
    persistent_worker_running: bool = False,
    static_builder: Callable[[], dict[str, Any]] = build_platformization_checkpoint,
    cn_builder: Callable[..., dict[str, Any]] = build_final_checkpoint,
) -> dict[str, Any]:
    static_checkpoint = static_builder()
    if not static_checkpoint.get("code_ready"):
        return evaluate_platformization_runtime_gate(
            static_checkpoint=static_checkpoint,
            cn_checkpoint=None,
        )

    cn_checkpoint = cn_builder(persistent_worker_running=persistent_worker_running)
    return evaluate_platformization_runtime_gate(
        static_checkpoint=static_checkpoint,
        cn_checkpoint=cn_checkpoint,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only M1.7 code + real CN runtime acceptance gate"
    )
    parser.add_argument(
        "--persistent-worker-running",
        action="store_true",
        help="Report that the persistent docker compose worker is running.",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    report = build_platformization_runtime_gate(
        persistent_worker_running=args.persistent_worker_running
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
