[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EvidenceRoot = 'reports'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $headSha = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMainSha = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$headSha"
    Write-Host "origin/main=$originMainSha"
    Write-Host "expected=$expected"
    if ($headSha -ne $expected -or $originMainSha -ne $expected) {
        throw "Exact main drift detected during $Phase."
    }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}

function Get-ProjectedFreeBytes {
    param(
        [Parameter(Mandatory = $true)][int64]$CurrentFreeBytes,
        [Parameter(Mandatory = $true)][int64]$PlannedReclaimBytes
    )
    if ($CurrentFreeBytes -lt 0 -or $PlannedReclaimBytes -lt 0) {
        throw 'Projected-free inputs must be non-negative.'
    }
    return [int64]($CurrentFreeBytes + $PlannedReclaimBytes)
}

function Get-ResidualDeficitBytes {
    param(
        [Parameter(Mandatory = $true)][int64]$CurrentDeficitBytes,
        [Parameter(Mandatory = $true)][int64]$PlannedReclaimBytes
    )
    if ($CurrentDeficitBytes -lt 0 -or $PlannedReclaimBytes -lt 0) {
        throw 'Residual-deficit inputs must be non-negative.'
    }
    return [int64][math]::Max([int64]0, [int64]($CurrentDeficitBytes - $PlannedReclaimBytes))
}

function Invoke-FreshRebalanceInventory([string]$RunId) {
    if ($RunId -notmatch '^[0-9A-Za-z_-]+$') { throw 'RunId contains unsupported characters.' }
    $childRelativeRoot = Join-Path (Join-Path 'reports' '_rp') $RunId
    $childAbsoluteRoot = Join-Path $repoRoot $childRelativeRoot
    New-Item -ItemType Directory -Force -Path $childAbsoluteRoot | Out-Null

    Write-Host 'rebalance_apply_plan_stage=fresh_candidate_inventory'
    Write-Host 'candidate_inventory_evidence_strategy=SHALLOW_REPO_REPORTS'
    Write-Host "candidate_inventory_evidence_root=$childAbsoluteRoot"

    $scriptPath = Join-Path $PSScriptRoot 'profile-production-storage-rebalance-candidates.ps1'
    $childArgs = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-AcceptedVolume',$AcceptedVolume,
        '-EvidenceRoot',$childRelativeRoot
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $outputLines = @(& powershell.exe @childArgs 2>&1)
        $childExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }

    foreach ($line in @($outputLines | ForEach-Object { $_.ToString() })) { Write-Host $line }
    if ($childExitCode -ne 0) { throw "Fresh production rebalance inventory exited $childExitCode." }

    $directories = @(Get-ChildItem -LiteralPath $childAbsoluteRoot -Directory -Filter 'production_storage_rebalance_inventory_*' |
        Sort-Object LastWriteTime -Descending)
    if ($directories.Count -ne 1) {
        throw "Expected exactly one isolated rebalance inventory directory; observed $($directories.Count)."
    }
    $reportPath = Join-Path $directories[0].FullName 'production_storage_rebalance_candidate_inventory.json'
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw 'Fresh rebalance candidate inventory receipt is missing.'
    }
    try { $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Fresh rebalance candidate inventory receipt is invalid JSON: $($_.Exception.Message)" }
    return [ordered]@{ path=$reportPath; report=$report; evidence_root=$childAbsoluteRoot }
}

function Assert-TemporaryHardFloorCandidate([object]$Inventory) {
    if ($null -eq $Inventory) { throw 'Fresh rebalance candidate inventory is empty.' }
    if ([string]$Inventory.receipt_version -ne 'PRODUCTION_STORAGE_REBALANCE_CANDIDATE_INVENTORY_V1') {
        throw 'Unexpected rebalance candidate receipt version.'
    }
    if ([string]$Inventory.decision -ne 'REBALANCE_TEMPORARY_HARD_FLOOR_CANDIDATES_FOUND') {
        throw "Temporary-20-percent review requires REBALANCE_TEMPORARY_HARD_FLOOR_CANDIDATES_FOUND; observed $($Inventory.decision)."
    }
    if ([string]$Inventory.next_gate -ne 'PRODUCTION_REBALANCE_APPLY_PLAN_WITH_TEMPORARY_20_PERCENT_REVIEW') {
        throw 'Fresh rebalance candidate inventory points to a different next gate.'
    }
    if (-not [bool]$Inventory.read_only -or
        -not [bool]$Inventory.production_invariant_preserved -or
        -not [bool]$Inventory.env_unchanged) {
        throw 'Fresh rebalance candidate inventory did not preserve read-only production invariants.'
    }
    if (-not [bool]$Inventory.production.accepted_production_mount_ready -or
        [string]$Inventory.production.accepted_volume -ne $AcceptedVolume -or
        [int64]$Inventory.production.worker_container_count -ne 0) {
        throw 'Fresh rebalance candidate inventory did not prove the accepted production named-volume boundary.'
    }
}

try {
    Write-Host '===== PRODUCTION STORAGE REBALANCE APPLY PLAN ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'Production storage rebalance apply plan must run from local main.'
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Production storage rebalance apply plan requires elevated Administrator PowerShell.'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $runId = "${timestamp}_$PID"
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_storage_rebalance_apply_plan_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

    $inventoryResult = Invoke-FreshRebalanceInventory $runId
    $inventory = $inventoryResult['report']
    Assert-TemporaryHardFloorCandidate $inventory

    Write-Host 'rebalance_apply_plan_stage=candidate_contract'
    $dCandidate = $inventory.preferred_candidates.D.legacy_raw
    $eHotCandidate = $inventory.preferred_candidates.E.legacy_ntfs_clickhouse
    $eLogsCandidate = $inventory.preferred_candidates.E.legacy_ntfs_clickhouse_logs

    if (-not [bool]$dCandidate.preferred_candidate -or
        -not [bool]$dCandidate.metadata_parity_exact -or
        [int64]$dCandidate.unexpected_compose_reference_count -ne 0 -or
        [int64]$dCandidate.unexpected_container_reference_count -ne 0) {
        throw 'D legacy Raw no longer satisfies the preferred duplicate-source candidate contract.'
    }
    if (-not [bool]$inventory.preferred_candidates.D.hard_deficit_covered -or
        [bool]$inventory.preferred_candidates.D.recommended_deficit_covered) {
        throw 'D candidate no longer represents the temporary-20-percent-only condition.'
    }
    if (-not [bool]$eHotCandidate.preferred_candidate) {
        throw 'E legacy NTFS ClickHouse is not a preferred unreferenced candidate.'
    }
    if ([bool]$eLogsCandidate.stats.exists -and [int64]$eLogsCandidate.stats.total_bytes -gt 0 -and
        -not [bool]$eLogsCandidate.preferred_candidate) {
        throw 'E legacy NTFS ClickHouse logs exist but are still referenced.'
    }
    if (-not [bool]$inventory.preferred_candidates.E.recommended_deficit_covered) {
        throw 'Preferred E candidates no longer cover the recommended 30-percent coexistence deficit.'
    }

    $dReclaimBytes = [int64]$inventory.preferred_candidates.D.total_preferred_reclaimable_bytes
    $eReclaimBytes = [int64]$inventory.preferred_candidates.E.total_preferred_reclaimable_bytes
    $dRecommendedDeficit = [int64]$inventory.deficits.d_additional_free_recommended_bytes
    $dHardDeficit = [int64]$inventory.deficits.d_additional_free_hard_bytes
    $eRecommendedDeficit = [int64]$inventory.deficits.e_additional_free_recommended_bytes
    $eHardDeficit = [int64]$inventory.deficits.e_additional_free_hard_bytes

    if ($dReclaimBytes -lt $dHardDeficit -or $dReclaimBytes -ge $dRecommendedDeficit) {
        throw 'D reclaim bytes no longer fit the intended temporary hard-floor review envelope.'
    }
    if ($eReclaimBytes -lt $eRecommendedDeficit) {
        throw 'E preferred reclaim bytes no longer cover the recommended deficit.'
    }

    Write-Host 'rebalance_apply_plan_stage=projected_post_rebalance_math'
    $dProjectedFree = Get-ProjectedFreeBytes ([int64]$inventory.drives.D.free_bytes) $dReclaimBytes
    $eProjectedFree = Get-ProjectedFreeBytes ([int64]$inventory.drives.E.free_bytes) $eReclaimBytes
    $dRecommendedResidual = Get-ResidualDeficitBytes $dRecommendedDeficit $dReclaimBytes
    $dHardResidual = Get-ResidualDeficitBytes $dHardDeficit $dReclaimBytes
    $eRecommendedResidual = Get-ResidualDeficitBytes $eRecommendedDeficit $eReclaimBytes
    $eHardResidual = Get-ResidualDeficitBytes $eHardDeficit $eReclaimBytes

    if ($dHardResidual -ne 0 -or $eRecommendedResidual -ne 0 -or $eHardResidual -ne 0) {
        throw 'Projected preferred-candidate reclaim does not satisfy the required temporary/recommended floors.'
    }
    if ($dRecommendedResidual -le 0) {
        throw 'Temporary review is no longer necessary because D would satisfy the recommended floor.'
    }

    $protectedVisualProcessedPath = [string]$dCandidate.protected_visual_processed_stats.normalized_path
    if (-not $protectedVisualProcessedPath) {
        $protectedVisualProcessedPath = [string]$dCandidate.protected_visual_processed_stats.path
    }

    $plannedActions = @(
        [ordered]@{
            phase=1
            drive='E'
            action='DELETE_EXACT_UNREFERENCED_LEGACY_NTFS_CLICKHOUSE_TREE'
            path=[string]$eHotCandidate.stats.normalized_path
            expected_bytes=[int64]$eHotCandidate.stats.total_bytes
            preconditions=@(
                'fresh candidate inventory remains accepted',
                'all Docker and Compose references to candidate path remain zero',
                'production ClickHouse remains healthy on accepted named volume',
                'worker and Raw writers remain quiescent',
                'path equals the exact legacy E NTFS ClickHouse root'
            )
        },
        [ordered]@{
            phase=1
            drive='E'
            action='DELETE_EXACT_UNREFERENCED_LEGACY_NTFS_CLICKHOUSE_LOG_TREE_IF_PRESENT'
            path=[string]$eLogsCandidate.stats.normalized_path
            expected_bytes=[int64]$eLogsCandidate.stats.total_bytes
            preconditions=@(
                'path exists only if still classified as a preferred unreferenced candidate',
                'all Docker and Compose references remain zero',
                'production invariant is rechecked after E phase'
            )
        },
        [ordered]@{
            phase=2
            drive='D'
            action='DELETE_ONLY_FULL_SHA256_VERIFIED_DUPLICATE_RAW_FILES'
            source_root=[string]$dCandidate.stats.normalized_path
            target_root=[string]$dCandidate.f_target_stats.normalized_path
            protected_subtree=$protectedVisualProcessedPath
            expected_reclaimable_bytes=$dReclaimBytes
            preconditions=@(
                'RAW_DATA_PATH and VISUAL_RAW_PATH still resolve to F target',
                'api worker mark-image-worker and qcc-acquisition are all absent/stopped',
                'source and target manifests are stable',
                'source and target file metadata are exact',
                'every deletable source file has a target counterpart with equal SHA256',
                'hash mismatch count is zero',
                'protected visual_processed subtree is excluded even when empty',
                'files are deleted individually from the verified manifest; no recursive raw-root delete'
            )
        }
    )

    $receipt = [ordered]@{
        plan_version='PRODUCTION_STORAGE_REBALANCE_APPLY_PLAN_V1'
        decision='PRODUCTION_REBALANCE_TEMPORARY_20_PERCENT_APPLY_PLAN_READY'
        next_gate='PRODUCTION_REBALANCE_GUARDED_APPLY_WITH_TEMPORARY_20_PERCENT_ACK'
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        read_only_plan=$true
        temporary_20_percent_review_required=$true
        temporary_20_percent_review_reason='Preferred D reclaim covers the 20% host hard floor but not the 30% recommended coexistence floor; preferred E reclaim covers the 30% recommended floor.'
        source_candidate_receipt_path=$inventoryResult['path']
        source_candidate_decision=[string]$inventory.decision
        accepted_volume=$AcceptedVolume
        projections=[ordered]@{
            d_current_free_bytes=[int64]$inventory.drives.D.free_bytes
            d_planned_reclaim_bytes=$dReclaimBytes
            d_projected_free_bytes=$dProjectedFree
            d_recommended_deficit_before_bytes=$dRecommendedDeficit
            d_hard_deficit_before_bytes=$dHardDeficit
            d_recommended_residual_after_bytes=$dRecommendedResidual
            d_hard_residual_after_bytes=$dHardResidual
            e_current_free_bytes=[int64]$inventory.drives.E.free_bytes
            e_planned_reclaim_bytes=$eReclaimBytes
            e_projected_free_bytes=$eProjectedFree
            e_recommended_deficit_before_bytes=$eRecommendedDeficit
            e_hard_deficit_before_bytes=$eHardDeficit
            e_recommended_residual_after_bytes=$eRecommendedResidual
            e_hard_residual_after_bytes=$eHardResidual
        }
        actions=$plannedActions
        retained=[ordered]@{
            visual_processed_subtree=$protectedVisualProcessedPath
            e_docker_cold_backup_role=[string]$inventory.retained_or_secondary.e_docker_cold_backup.role
            d_spike_role=[string]$inventory.retained_or_secondary.d_spike.role
            d_runtime_role=[string]$inventory.retained_or_secondary.d_runtime.role
            e_spike_role=[string]$inventory.retained_or_secondary.e_spike.role
            e_tooling_role=[string]$inventory.retained_or_secondary.e_tooling.role
            f_recovery_role=[string]$inventory.retained_or_secondary.f_recovery.role
        }
        guarded_apply_requirements=@(
            'exact main SHA and clean tracked tree',
            'Administrator PowerShell',
            'fresh candidate inventory repeats the accepted temporary-hard-floor decision',
            'all Raw consumer services remain quiescent',
            'production ClickHouse healthy before, between, and after phases',
            'accepted production ClickHouse mount remains the accepted named volume',
            'E legacy NTFS ClickHouse and logs have zero current Docker/Compose references immediately before deletion',
            'D legacy Raw and F Raw target pass full SHA256 parity immediately before any D source-file deletion',
            'D visual_processed subtree is never deleted',
            'E phase must be accepted before D phase begins',
            'post-rebalance free space must satisfy D 20% hard floor and E 30% recommended floor',
            'D 30% recommended floor remains an explicit unresolved coexistence gap until later source retirement/rebalance'
        )
        constraints=[ordered]@{
            apply_authorized=$false
            temporary_20_percent_acknowledgement_authorized=$false
            legacy_e_hot_delete_authorized=$false
            legacy_raw_delete_authorized=$false
            visual_processed_delete_authorized=$false
            docker_cold_backup_delete_authorized=$false
            docker_cold_backup_move_authorized=$false
            accepted_volume_delete_authorized=$false
            accepted_volume_move_authorized=$false
            docker_data_vhdx_move_authorized=$false
            docker_data_vhdx_compact_authorized=$false
            vhdx_create_authorized=$false
            vhdx_resize_authorized=$false
            vhdx_mount_authorized=$false
            wsl_unmount_authorized=$false
            wsl_shutdown_authorized=$false
            docker_restart_authorized=$false
            docker_prune_authorized=$false
            clickhouse_mutation_authorized=$false
            corpus_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
        mutation_performed=$false
    }

    $receiptPath = Join-Path $evidenceDir 'production_storage_rebalance_apply_plan.json'
    $receipt | ConvertTo-Json -Depth 24 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    $gib = [math]::Pow(1024,3)
    Write-Host '===== PRODUCTION STORAGE REBALANCE APPLY PLAN RESULT ====='
    Write-Host 'decision=PRODUCTION_REBALANCE_TEMPORARY_20_PERCENT_APPLY_PLAN_READY'
    Write-Host 'next_gate=PRODUCTION_REBALANCE_GUARDED_APPLY_WITH_TEMPORARY_20_PERCENT_ACK'
    Write-Host 'temporary_20_percent_review_required=True'
    Write-Host ("d_planned_reclaim_gib={0:N2}" -f ($dReclaimBytes / $gib))
    Write-Host ("d_projected_free_gib={0:N2}" -f ($dProjectedFree / $gib))
    Write-Host ("d_recommended_residual_after_gib={0:N2}" -f ($dRecommendedResidual / $gib))
    Write-Host ("d_hard_residual_after_gib={0:N2}" -f ($dHardResidual / $gib))
    Write-Host ("e_planned_reclaim_gib={0:N2}" -f ($eReclaimBytes / $gib))
    Write-Host ("e_projected_free_gib={0:N2}" -f ($eProjectedFree / $gib))
    Write-Host ("e_recommended_residual_after_gib={0:N2}" -f ($eRecommendedResidual / $gib))
    Write-Host "phase_1_e_hot_path=$([string]$eHotCandidate.stats.normalized_path)"
    Write-Host "phase_1_e_logs_path=$([string]$eLogsCandidate.stats.normalized_path)"
    Write-Host "phase_2_d_raw_source=$([string]$dCandidate.stats.normalized_path)"
    Write-Host "phase_2_f_raw_target=$([string]$dCandidate.f_target_stats.normalized_path)"
    Write-Host "phase_2_protected_visual_processed=$protectedVisualProcessedPath"
    Write-Host 'apply_authorized=False'
    Write-Host 'legacy_e_hot_delete_authorized=False'
    Write-Host 'legacy_raw_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'
    Write-Host 'mutation_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_STORAGE_REBALANCE_APPLY_PLAN_DONE'

    Assert-ExactMain 'exit'
}
finally { Pop-Location }
