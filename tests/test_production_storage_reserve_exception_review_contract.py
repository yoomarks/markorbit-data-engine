from pathlib import Path


SCRIPT = Path("scripts/preflight-production-storage-reserve-exception-review.ps1")
WORKFLOW = Path(".github/workflows/production-storage-reserve-exception-review-runtime.yml")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_layout_replan_is_strictly_read_only() -> None:
    text = _text()
    assert "[switch]$Apply" not in text
    assert "Remove-Item" not in text
    assert "[System.IO.File]::Delete" not in text
    assert "docker prune" not in text.lower()
    assert "docker restart" not in text.lower()
    assert "wsl --shutdown" not in text.lower()
    assert "wsl --unmount" not in text.lower()
    assert "OPTIMIZE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "read_only=True" in text
    assert "mutation_performed=False" in text
    assert "e_backup_delete_authorized=False" in text
    assert "cn_warm_move_authorized=False" in text
    assert "vhdx_create_authorized=False" in text
    assert "accepted_volume_mutation_authorized=False" in text


def test_layout_replan_binds_exact_post_d_refresh_provenance() -> None:
    text = _text()
    assert "a18e51a42bee13b9062ad271fd378840a8119d7f" in text
    assert "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_V1" in text
    assert "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_BLOCKED" in text
    assert "PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW" in text
    assert "refresh_to_current_changed_file_count" in text
    assert "refresh_to_current_unexpected_changed_file_count" in text
    assert "refresh_to_current_missing_tooling_file_count" in text
    assert "$changed.Count -ne 3" in text


def test_e_backup_and_f_recovery_are_exact_frozen_paths() -> None:
    text = _text()
    assert "E:\\DockerDataBackup\\DockerDesktopWSL_20260901_before_recovery" in text
    assert "F:\\MarkOrbitData\\recovery" in text
    assert "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx" in text
    assert "Get-DirectoryInventoryNoFollow" in text
    assert "Get-BackupReferenceInventory" in text
    assert "Get-WslBasePaths" in text
    assert "e_backup_reference_count" in text
    assert "e_backup_technical_reclaim_candidate" in text
    assert "e_backup_duplicate_identity_proven=False" in text
    assert "drive_E_backup_reclaim_projection_is_not_delete_authority=True" in text


def test_cn_user_architecture_moves_goods_and_events_to_warm_model_only() -> None:
    text = _text()
    assert "WARM_GOODS_CATEGORY" in text
    assert "WARM_EVENT_HISTORY" in text
    assert "HOT_CURRENT_SERVING_CONSERVATIVE" in text
    assert "cn_observed_event" in text
    assert "cn_goods_" in text
    assert "EndsWith('_event'" in text
    assert "cn_layout table=" in text
    assert "cn_layout_hot_bytes" in text
    assert "cn_layout_warm_bytes" in text
    assert "move_authorized=$false" in text


def test_layout_replan_uses_fresh_system_metadata_without_mutation() -> None:
    text = _text()
    assert "app.cn.capacity_profile" in text
    assert "CN_HOT_WARM_CAPACITY_PROFILE_V1" in text
    assert "full_corpus_scan" in text
    assert "mutation_performed" in text
    assert "Get-DriveSnapshot 'D'" in text
    assert "Get-DriveSnapshot 'E'" in text
    assert "Get-DriveSnapshot 'F'" in text
    assert "Assert-RawConsumersStopped" in text
    assert "Get-ProductionClickHouseHealth" in text
    assert "Assert-AcceptedProductionMount" in text
    assert "Assert-ComposeRawBindings" in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text


def test_layout_replan_models_d_hot_and_e_warm_with_safety_margin() -> None:
    text = _text()
    assert "CopySafetyMarginPercent" in text
    assert "Add-CopySafetyMargin" in text
    assert "$dHotPayload =" in text
    assert "$eWarmPayload =" in text
    assert "$dHotPhysicalRequired = Add-CopySafetyMargin $dHotPayload" in text
    assert "$eWarmPhysicalRequired = Add-CopySafetyMargin $eWarmPayload" in text
    assert "drive_D_recommended_new_budget_bytes" in text
    assert "drive_D_hard_new_budget_bytes" in text
    assert "scenario_d_recommended_margin_bytes" in text
    assert "scenario_e_projected_recommended_margin_bytes" in text
    assert "scenario_d_recommended_fit" in text
    assert "scenario_e_projected_recommended_fit" in text


def test_replan_only_advances_to_separate_e_backup_reclaim_preflight() -> None:
    text = _text()
    assert "PRODUCTION_STORAGE_LAYOUT_REPLAN_READY" in text
    assert "PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT" in text
    assert "PRODUCTION_E_BACKUP_PROVENANCE_REVIEW" in text
    assert "PRODUCTION_STORAGE_COEXISTENCE_REDESIGN" in text
    assert "PRODUCTION_CN_WARM_SCOPE_EXPANSION_REVIEW" in text
    assert "PRODUCTION_VHDX_PROVISIONING_PREFLIGHT" not in text


def test_contract_fixture_locks_classification_and_capacity_math() -> None:
    text = _text()
    assert "Get-UserArchitectureTier 'cn_goods_item_current'" in text
    assert "Get-UserArchitectureTier 'cn_observed_event'" in text
    assert "Get-UserArchitectureTier 'cn_goods_scope_event'" in text
    assert "Get-UserArchitectureTier 'cn_case_current'" in text
    assert "Get-NewAllocationBudget 1000 700 30" in text
    assert "Get-RequiredCapacityBytes 700 30" in text
    assert "PRODUCTION_STORAGE_LAYOUT_REPLAN_PS51_CONTRACT_PASS" in text


def test_workflow_exists_and_invokes_contract_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in workflow
    assert "shell: powershell" in workflow
    assert "-ContractOnly" in workflow
    assert "preflight-production-storage-reserve-exception-review.ps1" in workflow
    assert "concurrency:" in workflow
