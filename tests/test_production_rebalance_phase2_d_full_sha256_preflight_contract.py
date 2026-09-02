from pathlib import Path


SCRIPT = Path("scripts/preflight-production-rebalance-phase2-d-full-sha256.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_phase2d_preflight_is_read_only_and_has_no_apply_surface() -> None:
    text = _text()
    assert "[switch]$Apply" not in text
    assert "apply_supported=False" in text
    assert "mutation_performed=False" in text
    assert "phase2_d_file_delete_authorized=False" in text
    assert "recursive_legacy_raw_root_delete_authorized=False" in text
    assert "visual_processed_delete_authorized=False" in text
    for forbidden in (
        "Remove-Item",
        "robocopy",
        "Set-Content -LiteralPath $envPath",
        "docker compose up",
        "docker compose down",
        "docker compose restart",
        "docker system prune",
        "docker volume rm",
        "wsl.exe --shutdown",
        "wsl.exe --unmount",
        "New-VHD",
        "Resize-VHD",
        "Mount-VHD",
        "Dismount-VHD",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
    ):
        assert forbidden not in text


def test_phase2d_preflight_accepts_only_reparse_safe_phase1_go_receipt() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_APPLY_V1",
        "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_GO",
        "PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_APPLY",
        "boundary_manifest_match",
        "native_lx_verified_count",
        "recommended_floor_met",
        "Assert-Phase1ReceiptProvenance",
        "git' @('merge-base','--is-ancestor'",
        "phase1_to_current_unexpected_changed_file_count=",
    ):
        assert marker in text


def test_phase1_to_phase2_tooling_delta_is_tightly_allowlisted() -> None:
    text = _text()
    for allowed in (
        "scripts/preflight-production-rebalance-phase2-d-full-sha256.ps1",
        "tests/test_production_rebalance_phase2_d_full_sha256_preflight_contract.py",
        ".github/workflows/production-rebalance-phase2-d-full-sha256-preflight-runtime.yml",
    ):
        assert allowed in text
    assert "Phase1E acceptance provenance invalidated by non-Phase2-preflight changes" in text


def test_phase2d_preflight_freezes_exact_d_f_and_visual_processed_boundaries() -> None:
    text = _text()
    for marker in (
        r"D:\yoomarks\markorbit-data-engine\raw_data",
        r"F:\MarkOrbitData\raw",
        "visual_processed",
        "RAW_DATA_PATH",
        "VISUAL_RAW_PATH",
        "VISUAL_PROCESSED_PATH",
        "/data/raw",
        "/data/visual-raw",
        "/data/visual-processed",
        "Legacy D Raw has references outside protected visual_processed subtree.",
    ):
        assert marker in text


def test_phase2d_preflight_requires_full_sha256_and_stable_source_manifest() -> None:
    text = _text()
    for marker in (
        "Get-RawDeletionManifest",
        "Get-FileHash -LiteralPath $entry.source_path -Algorithm SHA256",
        "Get-FileHash -LiteralPath $targetPath -Algorithm SHA256",
        "phase2_d_hash_progress=",
        "phase2_d_hash_mismatch_count=",
        "phase2_d_verified_bytes=",
        "Compare-RawMetadataManifests",
        "phase2_d_source_manifest_stable=",
        "phase2_d_verified_sha256_manifest.json",
    ):
        assert marker in text


def test_phase2d_preflight_requires_e_complete_and_temporary_d_hard_floor() -> None:
    text = _text()
    for marker in (
        "e_additional_free_recommended_bytes",
        "Legacy E roots must remain absent after accepted Phase1E.",
        "hard_deficit_covered",
        "recommended_deficit_covered",
        "d_required_hard_free_bytes=",
        "d_required_recommended_free_bytes=",
        "d_projected_free_after_verified_reclaim_bytes=",
        "d_hard_residual_after_projected_bytes=",
        "d_recommended_residual_after_projected_bytes=",
    ):
        assert marker in text


def test_phase2d_preflight_keeps_production_and_bulk_boundaries_closed() -> None:
    text = _text()
    for marker in (
        "Assert-RawConsumersStopped",
        "Get-ProductionClickHouseHealth",
        "Assert-AcceptedProductionMount",
        "env_unchanged=$true",
        "accepted_volume_delete_authorized=$false",
        "docker_restart_authorized=$false",
        "docker_prune_authorized=$false",
        "vhdx_create_authorized=$false",
        "wsl_shutdown_authorized=$false",
        "clickhouse_mutation_authorized=$false",
        "cn_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ):
        assert marker in text


def test_success_only_advances_to_resumable_apply_design() -> None:
    text = _text()
    assert "PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_READY" in text
    assert "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_DESIGN" in text
    assert "PRODUCTION_REBALANCE_PHASE2_D_GO" not in text
