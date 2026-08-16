from __future__ import annotations

import pytest

from app.platformization_checkpoint import (
    assert_platformization_code_ready,
    build_platformization_checkpoint,
)


def _platform(**overrides):
    value = {
        "version": "MARKORBIT_PLATFORMIZATION_M1.7",
        "foundation_contracts_complete": True,
    }
    value.update(overrides)
    return value


def _versions(release="M1.6"):
    return {"engine_release": release, "components": {}}


def _native(**overrides):
    value = {
        "status": "COMPLETE",
        "native_business_node_count": 18,
        "intentional_compatibility_node_count": 3,
        "reasons": [],
    }
    value.update(overrides)
    return value


def test_static_checkpoint_is_code_ready_without_claiming_runtime_acceptance() -> None:
    checkpoint = build_platformization_checkpoint(
        platform_builder=_platform,
        version_builder=_versions,
        native_cutover_builder=_native,
    )

    assert checkpoint["status"] == "CODE_READY_PENDING_RUNTIME_ACCEPTANCE"
    assert checkpoint["read_only"] is True
    assert checkpoint["static_only"] is True
    assert checkpoint["code_ready"] is True
    assert checkpoint["runtime_acceptance_required"] is True
    assert checkpoint["runtime_acceptance_evaluated"] is False
    assert checkpoint["runtime_acceptance_passed"] is None
    assert checkpoint["required_runtime_acceptance"] == "CN_M16_FINAL_CHECKPOINT_V1"
    assert checkpoint["real_corpus_success_claimed"] is False
    assert checkpoint["release_promotion_allowed"] is False
    assert checkpoint["engine_release"] == "M1.6"
    assert checkpoint["next_action"] == "RUN_REAL_CN_RUNTIME_ACCEPTANCE_SEPARATELY"
    assert checkpoint["reasons"] == []


def test_static_checkpoint_blocks_incomplete_native_cutover() -> None:
    checkpoint = build_platformization_checkpoint(
        platform_builder=_platform,
        version_builder=_versions,
        native_cutover_builder=lambda: _native(status="INCOMPLETE", reasons=[{"code": "DRIFT"}]),
    )

    assert checkpoint["status"] == "BLOCKED"
    assert checkpoint["code_ready"] is False
    assert any(reason["code"] == "CN_NATIVE_CUTOVER_INCOMPLETE" for reason in checkpoint["reasons"])


def test_static_checkpoint_blocks_early_release_promotion() -> None:
    checkpoint = build_platformization_checkpoint(
        platform_builder=_platform,
        version_builder=lambda: _versions("M1.7"),
        native_cutover_builder=_native,
    )

    assert checkpoint["code_ready"] is False
    assert any(
        reason["code"] == "ENGINE_RELEASE_PROMOTED_BEFORE_RUNTIME_ACCEPTANCE_BOUNDARY"
        for reason in checkpoint["reasons"]
    )


def test_static_checkpoint_blocks_foundation_or_node_count_drift() -> None:
    checkpoint = build_platformization_checkpoint(
        platform_builder=lambda: _platform(foundation_contracts_complete=False),
        version_builder=_versions,
        native_cutover_builder=lambda: _native(
            native_business_node_count=17,
            intentional_compatibility_node_count=4,
        ),
    )

    codes = {reason["code"] for reason in checkpoint["reasons"]}
    assert checkpoint["code_ready"] is False
    assert "FOUNDATION_CONTRACTS_INCOMPLETE" in codes
    assert "CN_NATIVE_BUSINESS_NODE_COUNT_DRIFT" in codes
    assert "CN_INTENTIONAL_COMPATIBILITY_NODE_COUNT_DRIFT" in codes


def test_repository_state_passes_static_checkpoint() -> None:
    checkpoint = assert_platformization_code_ready()
    assert checkpoint["code_ready"] is True
    assert checkpoint["runtime_acceptance_evaluated"] is False


def test_assertion_raises_on_blocked_checkpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.platformization_checkpoint.build_platformization_checkpoint",
        lambda: {"code_ready": False, "reasons": [{"code": "BLOCKED"}]},
    )
    with pytest.raises(RuntimeError, match="M1.7 platformization static checkpoint failed"):
        assert_platformization_code_ready()
