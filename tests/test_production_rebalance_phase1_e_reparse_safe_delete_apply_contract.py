from pathlib import Path


SCRIPT = Path("scripts/run-production-rebalance-phase1-e-reparse-safe-delete.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_apply_requires_explicit_ack_and_exact_main() -> None:
    text = _text()
    assert "[switch]$AcknowledgeLegacyEDuplicateDelete" in text
    assert "[switch]$Apply" in text
    assert "-Apply requires explicit -AcknowledgeLegacyEDuplicateDelete." in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'destructive_boundary'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "must run from local main" in text


def test_apply_is_bound_to_fresh_same_main_preflight_and_manifest_regeneration() -> None:
    text = _text()
    for marker in (
        "Invoke-FreshSafeDeletePreflight",
        "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_V1",
        "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_READY",
        "accepted_same_main_preflight_receipt=",
        "accepted_hot_manifest_sha256=",
        "accepted_logs_manifest_sha256=",
        "safe_apply_stage=destructive_boundary_manifest_regeneration",
        "boundary_manifest_match=True",
        "Boundary non-following manifest SHA changed after accepted same-main preflight.",
    ):
        assert marker in text


def test_native_unlink_revalidates_lx_tag_and_version_without_following_target() -> None:
    text = _text()
    for marker in (
        "FILE_FLAG_OPEN_REPARSE_POINT",
        "FSCTL_GET_REPARSE_POINT",
        "0xA000001D",
        "LxVersion",
        "UnlinkChecked",
        "RemoveDirectoryW",
        "DeleteFileW",
        "Native no-follow unlink failed",
        "native_lx_reparse_verified_count=",
    ):
        assert marker in text
    assert "[MarkOrbit.NativeSafeDelete]::UnlinkChecked" in text
    assert "[uint32]0xA000001D, [uint32]2, $true" in text


def test_apply_never_uses_recursive_delete_or_target_mutation_surfaces() -> None:
    text = _text()
    for forbidden in (
        "Remove-Item -LiteralPath $legacyEHotNormalized -Recurse",
        "Remove-Item -Recurse",
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
        "Optimize-VHD",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "2023_5.zip",
    ):
        assert forbidden not in text


def test_apply_rechecks_references_consumers_production_and_capacity() -> None:
    text = _text()
    for marker in (
        "Assert-RawConsumersStopped",
        "Get-AllContainerMounts",
        "Get-ComposeBindMounts",
        "boundary_hot_container_reference_count=",
        "boundary_hot_compose_reference_count=",
        "boundary_logs_container_reference_count=",
        "boundary_logs_compose_reference_count=",
        "Production ClickHouse lost health at destructive boundary.",
        "Assert-AcceptedProductionMount",
        "Invoke-FreshCapacityInventory",
        "e_required_recommended_free_bytes=",
        "e_projected_free_after_logical_bytes=",
        "Non-following logical reclaim no longer covers the E recommended free-space target.",
    ):
        assert marker in text


def test_apply_journals_partial_failure_and_does_not_claim_go() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_REBALANCE_PHASE1_E_SAFE_DELETE_JOURNAL_V1",
        "state='PREPARED'",
        "state='MUTATING'",
        "state='PARTIAL_FAILURE'",
        "Phase1E native no-follow delete entered partial-failure state.",
        "state='GO'",
        "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_GO",
    ):
        assert marker in text


def test_dry_run_has_no_mutation_and_advances_only_to_explicit_apply() -> None:
    text = _text()
    assert "if (-not $Apply)" in text
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_READY_FOR_APPLY" in text
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_APPLY'" in text
    assert "$applyAccepted=$false" in text
    assert "$mutationPerformed=$false" in text


def test_bulk_and_platform_mutation_boundaries_remain_closed() -> None:
    text = _text()
    for marker in (
        "accepted_volume_delete_authorized=$false",
        "accepted_volume_move_authorized=$false",
        "docker_restart_authorized=$false",
        "docker_prune_authorized=$false",
        "vhdx_create_authorized=$false",
        "vhdx_delete_authorized=$false",
        "vhdx_move_authorized=$false",
        "wsl_shutdown_authorized=$false",
        "wsl_unmount_authorized=$false",
        "clickhouse_mutation_authorized=$false",
        "cn_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ):
        assert marker in text
