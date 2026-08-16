from __future__ import annotations

from app.platformization_runtime_gate import (
    build_platformization_runtime_gate,
    evaluate_platformization_runtime_gate,
)


def _static(*, ready: bool = True):
    return {
        "version": "MARKORBIT_PLATFORMIZATION_CHECKPOINT_V1",
        "status": "CODE_READY_PENDING_RUNTIME_ACCEPTANCE" if ready else "BLOCKED",
        "code_ready": ready,
        "runtime_acceptance_evaluated": False,
        "release_promotion_allowed": False,
        "reasons": [] if ready else [{"code": "STATIC_BLOCK"}],
    }


def _cn(status: str = "PASS", *, ready: bool = True, acceptance_executed: bool = True):
    return {
        "checkpoint_version": "CN_M16_FINAL_CHECKPOINT_V1",
        "read_only": True,
        "status": status,
        "ready_for_next_domain": ready,
        "acceptance_executed": acceptance_executed,
        "reasons": [] if ready else [{"code": "CN_BLOCK"}],
    }


def test_pass_combines_static_readiness_and_real_cn_acceptance() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_checkpoint=_cn(),
    )

    assert report["version"] == "MARKORBIT_PLATFORMIZATION_RUNTIME_GATE_V1"
    assert report["status"] == "PASS"
    assert report["read_only"] is True
    assert report["static_code_ready"] is True
    assert report["runtime_acceptance_evaluated"] is True
    assert report["runtime_acceptance_passed"] is True
    assert report["release_promotion_eligible"] is True
    assert report["release_promoted"] is False
    assert report["real_cn_runtime_accepted"] is True
    assert report["real_cn_runtime_acceptance_source"] == "CN_M16_FINAL_CHECKPOINT_V1"
    assert report["reasons"] == []


def test_pass_with_warnings_is_still_promotion_eligible() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_checkpoint=_cn("PASS_WITH_WARNINGS"),
    )
    assert report["status"] == "PASS_WITH_WARNINGS"
    assert report["release_promotion_eligible"] is True


def test_blocked_static_checkpoint_short_circuits_runtime_builder() -> None:
    calls = {"cn": 0}

    def cn_builder(**kwargs):
        calls["cn"] += 1
        raise AssertionError("runtime builder must not run when static code is blocked")

    report = build_platformization_runtime_gate(
        static_builder=lambda: _static(ready=False),
        cn_builder=cn_builder,
    )

    assert calls["cn"] == 0
    assert report["status"] == "BLOCKED"
    assert report["runtime_acceptance_evaluated"] is False
    assert report["release_promotion_eligible"] is False


def test_cn_not_ready_blocks_release_promotion() -> None:
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_checkpoint=_cn("BLOCKED", ready=False, acceptance_executed=False),
    )
    codes = {reason["code"] for reason in report["reasons"]}
    assert report["status"] == "BLOCKED"
    assert report["runtime_acceptance_evaluated"] is True
    assert report["runtime_acceptance_passed"] is False
    assert report["release_promotion_eligible"] is False
    assert "CN_RUNTIME_ACCEPTANCE_NOT_EXECUTED" in codes
    assert "CN_RUNTIME_NOT_ACCEPTED" in codes


def test_wrong_or_mutating_cn_checkpoint_fails_closed() -> None:
    cn = _cn()
    cn["checkpoint_version"] = "WRONG"
    cn["read_only"] = False
    report = evaluate_platformization_runtime_gate(
        static_checkpoint=_static(),
        cn_checkpoint=cn,
    )
    codes = {reason["code"] for reason in report["reasons"]}
    assert report["release_promotion_eligible"] is False
    assert "CN_RUNTIME_CHECKPOINT_VERSION_MISMATCH" in codes
    assert "CN_RUNTIME_CHECKPOINT_NOT_READ_ONLY" in codes


def test_builder_passes_worker_state_to_authoritative_cn_checkpoint() -> None:
    observed = {}

    def cn_builder(**kwargs):
        observed.update(kwargs)
        return _cn()

    report = build_platformization_runtime_gate(
        persistent_worker_running=True,
        static_builder=_static,
        cn_builder=cn_builder,
    )
    assert observed == {"persistent_worker_running": True}
    assert report["release_promotion_eligible"] is True
