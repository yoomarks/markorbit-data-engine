from pathlib import Path


SCRIPT = Path("scripts/plan-production-hot-warm-sizing.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_sizing_plan_reuses_authoritative_read_only_inputs() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_HOT_WARM_SIZING_PLAN_V1",
        "profile-production-multi-disk-migration-readiness.ps1",
        "PRODUCTION_MULTI_DISK_MIGRATION_READINESS_READY_FOR_SIZING_PLAN",
        "app.cn.capacity_profile",
        "CN_HOT_WARM_CAPACITY_PROFILE_V1",
        "facts_snapshot_within_source_baseline",
        "app.us.remaining_capacity_inventory",
        "US_BOUNDED_CAPACITY_PILOT_RECEIPT_V1",
        "APPLICATION_ONLY_DO_NOT_GENERALIZE_TO_ASSIGNMENT_TTAB_OR_GLOBAL",
        "PRODUCTION_HOT_WARM_SIZING_PLAN_READY",
        "PRODUCTION_HOT_WARM_SIZING_CAPACITY_BLOCKED",
        "PRODUCTION_STORAGE_REBALANCE_PLAN",
        "PRODUCTION_VHDX_PROVISIONING_PREFLIGHT",
    ):
        assert marker in text


def test_sizing_plan_freezes_conservative_cn_us_and_warm_scope() -> None:
    text = _text()
    assert "$hotCnPayload = $cnCurrentBytes" in text
    assert "$projectedUsApplicationPayload" in text
    assert "conditional_cn_warm_demotion_authorized=$false" in text
    assert "assignment_capacity_inferred_from_application=$false" in text
    assert "ttab_capacity_inferred_from_application=$false" in text
    assert "global_capacity_inferred_from_us_application=$false" in text
    assert "future_warm_capacity_claimed_without_evidence=$false" in text
    assert "warm_future_us_global_sufficiency_claimed=$false" in text
    assert "source_volume_reclaim_counted_as_current_free_space=$false" in text


def test_sizing_plan_separates_final_capacity_from_current_provisioning() -> None:
    text = _text()
    assert "$eRecommendedFinalFits = [bool]($warmCandidateRecommended -le $eHostRecommendedUsable)" in text
    assert "$eHardFinalFits = [bool]($warmCandidateHard -le $eHostHardUsable)" in text
    assert "$eCurrentRecommendedProvisionFits = [bool]($warmCandidateRecommended -le $eCurrentRecommendedNewBudget)" in text
    assert "$eCurrentHardProvisionFits = [bool]($warmCandidateHard -le $eCurrentHardNewBudget)" in text
    assert "e_current_recommended_provision_fits" in text
    assert "e_current_hard_provision_fits" in text
    assert "REBALANCE_REQUIRED_BEFORE_PROVISION" in text


def test_sizing_plan_has_host_and_disk_reserve_math() -> None:
    text = _text()
    for marker in (
        "Get-RequiredCapacityBytes",
        "Get-HostUsableBytes",
        "Get-CurrentNewAllocationBudgetBytes",
        "HostRecommendedFreePercent = 30",
        "HostHardFreePercent = 20",
        "DiskRecommendedFreePercent = 30",
        "DiskHardFreePercent = 20",
        "d_coexistence_recommended_lower_bound_fits",
        "final_capacity_state",
        "coexistence_state",
    ):
        assert marker in text


def test_facts_snapshot_must_be_positive_and_within_source_baseline() -> None:
    text = _text()
    assert "$factsRows -gt 0 -and $factsBytes -gt 0" in text
    assert "$factsRows -le $sourceRows -and $factsBytes -le $sourceBytes" in text
    assert "markorbit_facts metadata snapshot is invalid or exceeds" in text


def test_sizing_plan_never_authorizes_or_performs_mutation() -> None:
    text = _text()
    required_false_markers = (
        "vhdx_create_authorized=$false",
        "vhdx_resize_authorized=$false",
        "vhdx_mount_authorized=$false",
        "live_migration_authorized=$false",
        "source_volume_delete_authorized=$false",
        "raw_delete_authorized=$false",
        "full_cn_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
        "vhdx_create_performed=$false",
        "vhdx_resize_performed=$false",
        "vhdx_mount_performed=$false",
        "vhdx_move_performed=$false",
        "wsl_unmount_performed=$false",
        "wsl_shutdown_performed=$false",
        "docker_restart_performed=$false",
        "docker_prune_performed=$false",
        "production_clickhouse_mutation_performed=$false",
        "accepted_volume_mutation_performed=$false",
        "source_copy_performed=$false",
        "corpus_replay_performed=$false",
    )
    for marker in required_false_markers:
        assert marker in text

    forbidden = (
        "New-VHD",
        "Resize-VHD",
        "Mount-VHD",
        "Dismount-VHD",
        "Format-Volume",
        "mkfs.ext4",
        "wsl.exe --shutdown",
        "wsl.exe --unmount",
        "docker prune",
        "docker volume rm",
        "docker compose down",
        "docker compose restart",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "robocopy",
        "Copy-Item",
        "Move-Item",
        "Remove-Item",
        "2023_5.zip",
    )
    for marker in forbidden:
        assert marker not in text


def test_disposable_worker_is_no_deps_and_removed() -> None:
    text = _text()
    assert "docker compose run --rm --no-deps -T" in text
    assert "Assert-NoWorkerContainers" in text
