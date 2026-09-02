from pathlib import Path


SCRIPT = Path("scripts/preflight-production-rebalance-post-d-reclaim-refresh.ps1")
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_refresh_is_read_only_and_has_no_apply_surface():
    assert "[switch]$ContractOnly" in TEXT
    assert "[switch]$Apply" not in TEXT
    assert "read_only=True" in TEXT
    assert "mutation_performed=False" in TEXT
    for marker in (
        "raw_delete_authorized=False",
        "vhdx_create_authorized=False",
        "vhdx_resize_authorized=False",
        "vhdx_mount_authorized=False",
        "accepted_volume_copy_authorized=False",
        "accepted_volume_move_authorized=False",
        "accepted_volume_delete_authorized=False",
        "docker_restart_authorized=False",
        "docker_prune_authorized=False",
        "wsl_attach_authorized=False",
        "wsl_unmount_authorized=False",
        "wsl_shutdown_authorized=False",
        "wsl_unregister_authorized=False",
        "clickhouse_cutover_authorized=False",
        "clickhouse_mutation_authorized=False",
        "cn_replay_authorized=False",
        "us_package_2_authorized=False",
        "us_bulk_authorized=False",
    ):
        assert marker in TEXT


def test_no_production_delete_or_migration_api_exists():
    forbidden = (
        "[System.IO.File]::Delete",
        "[System.IO.Directory]::Delete",
        "Remove-Item",
        "wsl.exe --shutdown",
        "wsl.exe --unmount",
        "wsl --shutdown",
        "wsl --unmount",
        "docker volume rm",
        "docker system prune",
        "docker compose down",
        "Optimize-Volume",
    )
    for marker in forbidden:
        assert marker not in TEXT


def test_phase2d_go_authority_is_frozen_exactly():
    assert "$script:AcceptedApplyEngineSha = 'ff2f6d1f35f69d865d31b6e38f1549c2382577d8'" in TEXT
    assert "$script:AcceptedAuthorityEngineSha = '74cc3379fc7ff81f29a9235b7c55a0ffda2f4090'" in TEXT
    assert "$script:AcceptedAuthorityManifestSha256 = '6cd4399aaaf47aab3c5dde6dfd87dc7a29be676ce0d3da93d3d6e493f2f35253'" in TEXT
    assert "$script:AcceptedManifestFileCount = [int64]1146" in TEXT
    assert "$script:AcceptedManifestBytes = [int64]57920246250" in TEXT
    assert "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_APPLY_V1" in TEXT
    assert "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_GO" in TEXT
    assert "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH" in TEXT


def test_provenance_allows_only_three_refresh_tooling_files():
    for path in (
        "scripts/preflight-production-rebalance-post-d-reclaim-refresh.ps1",
        "tests/test_production_rebalance_post_d_reclaim_refresh_contract.py",
        ".github/workflows/production-rebalance-post-d-reclaim-refresh-runtime.yml",
    ):
        assert path in TEXT
    assert "apply_to_current_unexpected_changed_file_count" in TEXT
    assert "apply_to_current_missing_tooling_file_count" in TEXT
    assert "git' @('merge-base','--is-ancestor'" in TEXT
    assert "git' @('diff','--name-only'" in TEXT


def test_go_receipt_and_journal_fail_closed_on_invariant_drift():
    for marker in (
        "Phase2D Apply receipt is not GO",
        "Phase2D Apply deletion dimensions changed",
        "Phase2D Apply reserve semantics changed",
        "Phase2D journal is not final GO",
        "GO journal deletion counters changed",
        "GO journal still contains inflight path",
        "GO journal completed path count changed",
        "Frozen authority manifest SHA changed",
    ):
        assert marker in TEXT


def test_post_delete_state_requires_d_absent_f_present_and_protected_unchanged():
    assert "Deleted D authority file reappeared" in TEXT
    assert "F authority counterpart missing" in TEXT
    assert "F authority counterpart is not a normal file" in TEXT
    assert "F authority counterpart length changed" in TEXT
    assert "D Raw contains files outside protected visual_processed after accepted reclaim" in TEXT
    assert "Protected visual_processed changed after accepted reclaim" in TEXT
    assert "post_reclaim_authority_progress=" in TEXT
    assert "unexpected_d_raw_file_count=" in TEXT


def test_runtime_bindings_and_production_invariants_are_rechecked_before_and_after_sizing():
    assert "Assert-ProductionBoundary $authority.journal 'post_reclaim_before'" in TEXT
    assert "Assert-ProductionBoundary $authority.journal 'post_reclaim_final'" in TEXT
    assert "Assert-RawConsumersStopped" in TEXT
    assert "Get-ProductionClickHouseHealth" in TEXT
    assert "Assert-AcceptedProductionMount" in TEXT
    assert "RAW_DATA_PATH no longer points to accepted F Raw" in TEXT
    assert "VISUAL_RAW_PATH no longer points to accepted F Raw" in TEXT
    assert "VISUAL_PROCESSED_PATH no longer points to protected D subtree" in TEXT
    assert "Legacy E hot/log roots reappeared" in TEXT


def test_existing_hot_warm_sizing_is_reused_under_shallow_isolated_evidence():
    assert "plan-production-hot-warm-sizing.ps1" in TEXT
    assert "Join-Path (Join-Path 'reports' '_pdr')" in TEXT
    assert "Fresh production Hot/Warm sizing" in TEXT
    assert "PRODUCTION_HOT_WARM_SIZING_PLAN_V1" in TEXT
    assert "source_copy_performed" in TEXT
    assert "corpus_replay_performed" in TEXT


def test_only_recommended_30_percent_fit_advances_to_vhdx_preflight():
    assert "PRODUCTION_HOT_WARM_SIZING_PLAN_READY" in TEXT
    assert "RECOMMENDED_30_PERCENT_PLAN_FITS" in TEXT
    assert "CURRENT_HOST_CAN_PROVISION_WITH_RECOMMENDED_RESERVE" in TEXT
    assert "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_READY" in TEXT
    assert "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_BLOCKED" in TEXT
    assert "PRODUCTION_VHDX_PROVISIONING_PREFLIGHT" in TEXT
    assert "$recommendedAdmission" in TEXT


def test_refresh_receipt_exports_fresh_drives_source_and_quotas_without_authority():
    for marker in (
        "drive_D_free_bytes=",
        "drive_E_free_bytes=",
        "drive_F_free_bytes=",
        "source_active_rows=",
        "source_active_bytes_on_disk=",
        "recommended_hot_cn_capacity_bytes=",
        "recommended_hot_us_application_capacity_bytes=",
        "recommended_hot_global_bootstrap_capacity_bytes=",
        "recommended_warm_candidate_capacity_bytes=",
        "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_DONE",
    ):
        assert marker in TEXT
