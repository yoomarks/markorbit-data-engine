from pathlib import Path


SCRIPT = Path("scripts/preflight-production-storage-reserve-exception-review.ps1")
WORKFLOW = Path(".github/workflows/production-storage-reserve-exception-review-runtime.yml")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_reserve_review_is_strictly_read_only() -> None:
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
    assert " MOVE " not in text
    assert "read_only=True" in text
    assert "mutation_performed=False" in text
    assert "temporary_20_percent_exception_granted=False" in text
    assert "vhdx_create_authorized=False" in text
    assert "accepted_volume_mutation_authorized=False" in text


def test_reserve_review_binds_exact_post_d_refresh_provenance() -> None:
    text = _text()
    assert "a18e51a42bee13b9062ad271fd378840a8119d7f" in text
    assert "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_V1" in text
    assert "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_BLOCKED" in text
    assert "PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW" in text
    assert "refresh_to_current_changed_file_count" in text
    assert "refresh_to_current_unexpected_changed_file_count" in text
    assert "refresh_to_current_missing_tooling_file_count" in text
    assert "$changed.Count -ne 3" in text


def test_reserve_review_reuses_fresh_sizing_and_runtime_gates() -> None:
    text = _text()
    assert "plan-production-hot-warm-sizing.ps1" in text
    assert "PRODUCTION_HOT_WARM_SIZING_PLAN_V1" in text
    assert "PRODUCTION_HOT_WARM_SIZING_PLAN_READY" in text
    assert "RECOMMENDED_30_PERCENT_PLAN_FITS" in text
    assert "Assert-RawConsumersStopped" in text
    assert "Get-ProductionClickHouseHealth" in text
    assert "Assert-AcceptedProductionMount" in text
    assert "Assert-ComposeRawBindings" in text
    assert "RAW_DATA_PATH" in text
    assert "VISUAL_RAW_PATH" in text
    assert "VISUAL_PROCESSED_PATH" in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text


def test_reserve_review_uses_physical_and_active_copy_math() -> None:
    text = _text()
    assert "$sourceDiskUsed = [int64]($sourceDiskTotal - $sourceDiskFree)" in text
    assert "$sourceActive = [int64]$plan.current_payload.source_active_bytes_on_disk" in text
    assert "$warmCandidate = [int64]$plan.current_payload.cn_conditional_warm_candidate_bytes" in text
    assert "$warmSplitDRequired = [int64]($sourceActive - $warmCandidate)" in text
    assert "$warmSplitERequired = $warmCandidate" in text
    assert "full_physical_to_D" in text
    assert "active_only_to_D" in text
    assert "active_data_with_warm_split" in text
    assert "copy_contract_proven=$false" in text
    assert "vhdx_authorized=$false" in text


def test_full_physical_copy_cannot_be_silently_replaced_by_active_bytes() -> None:
    text = _text()
    assert "$fullPhysicalMarginHard = Get-SignedMargin $dHardBudget $sourceDiskUsed" in text
    assert "$activeDOnlyMarginHard = Get-SignedMargin $dHardBudget $sourceActive" in text
    assert "$fullPhysicalHardFit = [bool]($fullPhysicalMarginHard -ge 0)" in text
    assert "$activeDOnlyHardFit = [bool]($activeDOnlyMarginHard -ge 0)" in text
    assert "source_disk_used_bytes=$sourceDiskUsed" in text
    assert "source_active_bytes=$sourceActive" in text


def test_warm_split_only_advances_to_copy_contract_preflight() -> None:
    text = _text()
    assert "PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW_COPY_CONTRACT_REQUIRED" in text
    assert "PRODUCTION_ACTIVE_DATA_WARM_SPLIT_COPY_CONTRACT_PREFLIGHT" in text
    assert "PRODUCTION_STORAGE_COEXISTENCE_REDESIGN" in text
    assert "PRODUCTION_VHDX_PROVISIONING_PREFLIGHT" in text
    # Arithmetic warm-split fit alone must not grant the temporary exception.
    assert "temporary_20_percent_exception_granted=$false" in text


def test_read_only_du_inventory_is_metadata_only() -> None:
    text = _text()
    assert "du -sk" in text
    assert "/var/lib/clickhouse" in text
    assert "source_top_level_allocated=" in text
    assert "root_allocated_bytes" in text


def test_contract_fixture_locks_budget_and_signed_margin_math() -> None:
    text = _text()
    assert "Get-NewAllocationBudget 1000 700 20" in text
    assert "$budget -ne 500" in text
    assert "Get-SignedMargin 500 450" in text
    assert "Get-SignedMargin 500 550" in text
    assert "PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW_PS51_CONTRACT_PASS" in text


def test_workflow_exists_and_invokes_contract_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in workflow
    assert "shell: powershell" in workflow
    assert "-ContractOnly" in workflow
    assert "preflight-production-storage-reserve-exception-review.ps1" in workflow
    assert "concurrency:" in workflow
