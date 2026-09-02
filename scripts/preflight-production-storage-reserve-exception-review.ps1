[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedPostDRefreshReceiptPath,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyRawRoot = 'D:\yoomarks\markorbit-data-engine\raw_data',
    [string]$RawTargetRoot = 'F:\MarkOrbitData\raw',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedPostDRefreshEngineSha = 'a18e51a42bee13b9062ad271fd378840a8119d7f'
$script:AcceptedPostDRefreshReceiptVersion = 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_V1'
$script:ReserveReviewReceiptVersion = 'PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW_V1'
$script:AllowedReserveReviewToolingFiles = @(
    'scripts/preflight-production-storage-reserve-exception-review.ps1',
    'tests/test_production_storage_reserve_exception_review_contract.py',
    '.github/workflows/production-storage-reserve-exception-review-runtime.yml'
)

function Import-AcceptedPreflightHelpers {
    $helperScriptPath = Join-Path $PSScriptRoot 'preflight-production-rebalance-phase2-d-full-sha256.ps1'
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($helperScriptPath, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw 'Accepted Phase2D helper source no longer parses.' }
    $names = @(
        'Invoke-NativeText','Assert-ExactMain','Normalize-HostPath','Test-PathContains','Test-PathsOverlap',
        'Get-OptionalPropertyValue','Get-OptionalArrayProperty','Get-DotEnvValues','Get-ProductionClickHouseHealth',
        'Assert-AcceptedProductionMount','Assert-RawConsumersStopped','Get-AllContainerMounts','Get-ComposeBindMounts',
        'Assert-ComposeRawBindings'
    )
    $functions = $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $node.Name
    }, $true)
    foreach ($name in $names) {
        $matches = @($functions | Where-Object { $_.Name -eq $name })
        if ($matches.Count -ne 1) { throw "Expected exactly one accepted helper definition: $name" }
        $definitionText = [string]$matches[0].Extent.Text
        $pattern = '^(\s*function\s+)' + [regex]::Escape($name) + '(?=\s*(?:\(|\{))'
        $replacement = '${1}script:' + $name
        $scriptScopedDefinition = [regex]::Replace(
            $definitionText,
            $pattern,
            $replacement,
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
        if ($scriptScopedDefinition -eq $definitionText) { throw "Unable to scope accepted helper definition: $name" }
        Invoke-Expression $scriptScopedDefinition
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-JsonFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label file missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Get-ReserveBytes([int64]$TotalBytes, [double]$Percent) {
    if ($TotalBytes -le 0 -or $Percent -le 0 -or $Percent -ge 100) { throw 'Invalid reserve inputs.' }
    return [int64][math]::Ceiling([double]$TotalBytes * ($Percent / 100.0))
}

function Get-NewAllocationBudget([int64]$TotalBytes, [int64]$FreeBytes, [double]$Percent) {
    $reserve = Get-ReserveBytes $TotalBytes $Percent
    return [int64][math]::Max([int64]0, [int64]($FreeBytes - $reserve))
}

function Get-SignedMargin([int64]$BudgetBytes, [int64]$RequiredBytes) {
    return [int64]($BudgetBytes - $RequiredBytes)
}

function Assert-PostRefreshProvenance {
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:AcceptedPostDRefreshEngineSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Accepted post-D refresh SHA is not an ancestor of current exact main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"$($script:AcceptedPostDRefreshEngineSha)..$ExpectedMainSha")
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedReserveReviewToolingFiles })
    $missing = @($script:AllowedReserveReviewToolingFiles | Where-Object { $_ -notin $changed })
    Write-Host "refresh_to_current_changed_file_count=$($changed.Count)"
    Write-Host "refresh_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "refresh_to_current_missing_tooling_file_count=$($missing.Count)"
    if ($unexpected.Count -ne 0 -or $missing.Count -ne 0 -or $changed.Count -ne 3) {
        throw 'Post-D refresh provenance changed outside the reserve-review tooling boundary.'
    }
}

function Resolve-AcceptedPostDRefreshReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedPostDRefreshReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted post-D refresh receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedPostDRefreshReceiptVersion) { throw 'Unexpected post-D refresh receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedPostDRefreshEngineSha) { throw 'Post-D refresh receipt engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_BLOCKED') { throw 'Post-D refresh receipt is not the accepted reserve-review state.' }
    if ([string]$receipt.next_gate -ne 'PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW') { throw 'Post-D refresh next gate changed.' }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { throw 'Post-D refresh receipt lost read-only contract.' }
    if ([string]$receipt.fresh_sizing_decision -ne 'PRODUCTION_HOT_WARM_SIZING_PLAN_READY') { throw 'Post-D refresh sizing was not ready.' }
    if ([string]$receipt.final_capacity_state -ne 'RECOMMENDED_30_PERCENT_PLAN_FITS') { throw 'Final 30-percent architecture fit is no longer accepted.' }
    if ([string]$receipt.coexistence_state -ne 'CURRENT_HOST_HARD_FLOOR_ONLY') { throw 'Expected hard-floor-only coexistence state changed.' }
    if ([bool]$receipt.recommended_30_percent_admission) { throw 'Receipt unexpectedly admitted recommended coexistence.' }
    foreach ($name in @('raw_delete_authorized','vhdx_create_authorized','vhdx_mount_authorized','accepted_volume_delete_authorized','us_package_2_authorized','us_bulk_authorized')) {
        if ([bool](Get-OptionalPropertyValue $receipt.constraints $name)) { throw "Accepted refresh unexpectedly authorized $name." }
    }
    return [ordered]@{ path=$path; sha256=(Get-Sha256 $path); receipt=$receipt }
}

function Invoke-FreshSizing([string]$RunId) {
    $relativeRoot = Join-Path (Join-Path 'reports' '_rex') $RunId
    $absoluteRoot = Join-Path $repoRoot $relativeRoot
    New-Item -ItemType Directory -Force -Path $absoluteRoot | Out-Null
    Write-Host "reserve_review_sizing_evidence_root=$absoluteRoot"
    $scriptPath = Join-Path $PSScriptRoot 'plan-production-hot-warm-sizing.ps1'
    $args = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-AcceptedVolume',$AcceptedVolume,
        '-EvidenceRoot',$relativeRoot
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& powershell.exe @args 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    foreach ($line in @($output | ForEach-Object { $_.ToString() })) { Write-Host $line }
    if ($exitCode -ne 0) { throw "Fresh Hot/Warm sizing exited $exitCode." }
    $dirs = @(Get-ChildItem -LiteralPath $absoluteRoot -Directory -Filter 'production_hot_warm_sizing_*' | Sort-Object LastWriteTime -Descending)
    if ($dirs.Count -ne 1) { throw "Expected one isolated sizing directory; observed $($dirs.Count)." }
    $path = Join-Path $dirs[0].FullName 'production_hot_warm_sizing_plan.json'
    $plan = Read-JsonFile $path 'Fresh Hot/Warm sizing plan'
    if ([string]$plan.plan_version -ne 'PRODUCTION_HOT_WARM_SIZING_PLAN_V1' -or -not [bool]$plan.read_only) { throw 'Fresh sizing plan contract changed.' }
    if ([string]$plan.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Fresh sizing engine SHA changed.' }
    if (-not [bool]$plan.production_invariant_preserved -or -not [bool]$plan.env_unchanged) { throw 'Fresh sizing lost production/.env invariant.' }
    return [ordered]@{ path=$path; sha256=(Get-Sha256 $path); plan=$plan }
}

function Get-ClickHouseTopLevelAllocatedInventory {
    $shell = 'set -eu; root=/var/lib/clickhouse; kb=$(du -sk "$root" | cut -f1); printf "__ROOT__\t%s\n" "$kb"; for p in "$root"/*; do [ -e "$p" ] || continue; kb=$(du -sk "$p" | cut -f1); name=$(basename "$p"); printf "%s\t%s\n" "$name" "$kb"; done'
    $probe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','sh','-lc',$shell) -AllowFailure
    if ($probe.exit_code -ne 0) { throw 'Unable to collect read-only ClickHouse top-level allocated-byte inventory.' }
    $entries = @()
    foreach ($line in @($probe.lines | Where-Object { $_.Trim() })) {
        $parts = $line -split "`t",2
        if ($parts.Count -ne 2 -or $parts[1] -notmatch '^\d+$') { throw "Unexpected ClickHouse du output: $line" }
        $entries += [pscustomobject]@{ name=[string]$parts[0]; allocated_bytes=[int64]$parts[1] * 1024 }
    }
    $root = @($entries | Where-Object { $_.name -eq '__ROOT__' })
    if ($root.Count -ne 1) { throw 'ClickHouse root allocated-byte inventory missing.' }
    return [ordered]@{
        root_allocated_bytes=[int64]$root[0].allocated_bytes
        top_level=@($entries | Where-Object { $_.name -ne '__ROOT__' } | Sort-Object allocated_bytes -Descending)
    }
}

function Assert-CurrentRuntimeBoundary([string]$ProtectedVisualProcessed) {
    Assert-RawConsumersStopped
    $production = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_reserve_review=$([bool]$production.ready)"
    if (-not [bool]$production.ready) { throw 'Production ClickHouse must be healthy during reserve review.' }
    Assert-AcceptedProductionMount $production.container_id

    $compose = @(Get-ComposeBindMounts)
    Assert-ComposeRawBindings $compose $ProtectedVisualProcessed
    $containers = @(Get-AllContainerMounts)
    $dContainerRefs = @($containers | Where-Object { $_.normalized_source -and (Test-PathsOverlap $LegacyRawRoot $_.normalized_source) })
    $dComposeRefs = @($compose | Where-Object { $_.normalized_source -and (Test-PathsOverlap $LegacyRawRoot $_.normalized_source) })
    $unexpectedContainer = @($dContainerRefs | Where-Object { -not (Test-PathContains $ProtectedVisualProcessed $_.normalized_source) })
    $unexpectedCompose = @($dComposeRefs | Where-Object { -not (Test-PathContains $ProtectedVisualProcessed $_.normalized_source) })
    Write-Host "reserve_review_unexpected_d_container_reference_count=$($unexpectedContainer.Count)"
    Write-Host "reserve_review_unexpected_d_compose_reference_count=$($unexpectedCompose.Count)"
    if ($unexpectedContainer.Count -ne 0 -or $unexpectedCompose.Count -ne 0) { throw 'Unexpected D Raw references returned after reclaim.' }
}

function Invoke-ContractFixture {
    foreach ($name in @('Assert-ExactMain','Normalize-HostPath','Get-ProductionClickHouseHealth','Assert-AcceptedProductionMount','Assert-RawConsumersStopped')) {
        if ($null -eq (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Imported helper missing: $name" }
    }
    $budget = Get-NewAllocationBudget 1000 700 20
    if ($budget -ne 500) { throw 'Hard budget math contract failed.' }
    if ((Get-SignedMargin 500 450) -ne 50) { throw 'Signed margin positive contract failed.' }
    if ((Get-SignedMargin 500 550) -ne -50) { throw 'Signed margin negative contract failed.' }
    if ($script:AllowedReserveReviewToolingFiles.Count -ne 3) { throw 'Reserve review tooling provenance count changed.' }
    Write-Host 'PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW_PS51_CONTRACT_PASS'
}

try {
    Import-AcceptedPreflightHelpers

    if ($ContractOnly) {
        Invoke-ContractFixture
        return
    }

    Write-Host '===== PRODUCTION STORAGE RESERVE EXCEPTION REVIEW ====='
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'temporary_20_percent_exception_granted=False'
    Write-Host 'raw_delete_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'vhdx_resize_authorized=False'
    Write-Host 'vhdx_mount_authorized=False'
    Write-Host 'accepted_volume_mutation_authorized=False'
    Write-Host 'docker_restart_authorized=False'
    Write-Host 'docker_prune_authorized=False'
    Write-Host 'wsl_mutation_authorized=False'
    Write-Host 'clickhouse_mutation_authorized=False'
    Write-Host 'cn_replay_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Reserve exception review must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-PostRefreshProvenance

    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Reserve review requires elevated Administrator PowerShell.' }

    $legacyRaw = Normalize-HostPath $LegacyRawRoot
    $rawTarget = Normalize-HostPath $RawTargetRoot
    $protectedVisualProcessed = Normalize-HostPath (Join-Path $legacyRaw 'visual_processed')
    if (-not $legacyRaw.Equals('D:\yoomarks\markorbit-data-engine\raw_data', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyRawRoot changed.' }
    if (-not $rawTarget.Equals('F:\MarkOrbitData\raw', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RawTargetRoot changed.' }

    $refresh = Resolve-AcceptedPostDRefreshReceipt
    Write-Host "accepted_post_d_refresh_receipt=$($refresh.path)"
    Write-Host "accepted_post_d_refresh_receipt_sha256=$($refresh.sha256)"

    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env is required.' }
    $envHashBefore = Get-Sha256 $envPath
    $envValues = Get-DotEnvValues @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    if (-not (Normalize-HostPath ([string]$envValues['RAW_DATA_PATH'])).Equals($rawTarget, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RAW_DATA_PATH is not exact F Raw target.' }
    if ($envValues.ContainsKey('VISUAL_RAW_PATH') -and -not (Normalize-HostPath ([string]$envValues['VISUAL_RAW_PATH'])).Equals($rawTarget, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_RAW_PATH is not exact F Raw target.' }
    if ($envValues.ContainsKey('VISUAL_PROCESSED_PATH') -and -not (Normalize-HostPath ([string]$envValues['VISUAL_PROCESSED_PATH'])).Equals($protectedVisualProcessed, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_PROCESSED_PATH changed.' }

    Assert-ExactMain 'reserve_review_before'
    Assert-CurrentRuntimeBoundary $protectedVisualProcessed

    $runId = '{0}_{1}' -f (Get-Date -Format 'yyyyMMdd_HHmmssfff'), $PID
    $sizingResult = Invoke-FreshSizing $runId
    $plan = $sizingResult.plan
    if ([string]$plan.decision -ne 'PRODUCTION_HOT_WARM_SIZING_PLAN_READY') { throw 'Fresh sizing plan is not ready.' }
    if ([string]$plan.fit.final_capacity_state -ne 'RECOMMENDED_30_PERCENT_PLAN_FITS') { throw 'Final architecture no longer fits recommended reserve.' }

    $readinessPath = [string]$plan.readiness.receipt_path
    $readiness = Read-JsonFile $readinessPath 'Fresh migration readiness receipt'
    $sourceDisk = $readiness.production.source_disk
    $sourceDiskTotal = [int64]$sourceDisk.total_bytes
    $sourceDiskFree = [int64]$sourceDisk.free_bytes
    $sourceDiskUsed = [int64]($sourceDiskTotal - $sourceDiskFree)
    $sourceActive = [int64]$plan.current_payload.source_active_bytes_on_disk
    $warmCandidate = [int64]$plan.current_payload.cn_conditional_warm_candidate_bytes
    if ($sourceDiskTotal -le 0 -or $sourceDiskFree -lt 0 -or $sourceDiskUsed -le 0 -or $sourceActive -le 0) { throw 'Fresh source capacity evidence invalid.' }
    if ($warmCandidate -lt 0 -or $warmCandidate -gt $sourceActive) { throw 'Warm candidate bytes invalid.' }

    $dTotal = [int64]$plan.drives.D.total_bytes
    $dFree = [int64]$plan.drives.D.free_bytes
    $eTotal = [int64]$plan.drives.E.total_bytes
    $eFree = [int64]$plan.drives.E.free_bytes
    $dRecommendedBudget = Get-NewAllocationBudget $dTotal $dFree 30
    $dHardBudget = Get-NewAllocationBudget $dTotal $dFree 20
    $eRecommendedBudget = Get-NewAllocationBudget $eTotal $eFree 30
    $eHardBudget = Get-NewAllocationBudget $eTotal $eFree 20

    $du = Get-ClickHouseTopLevelAllocatedInventory
    $activeDOnlyMarginRecommended = Get-SignedMargin $dRecommendedBudget $sourceActive
    $activeDOnlyMarginHard = Get-SignedMargin $dHardBudget $sourceActive
    $fullPhysicalMarginRecommended = Get-SignedMargin $dRecommendedBudget $sourceDiskUsed
    $fullPhysicalMarginHard = Get-SignedMargin $dHardBudget $sourceDiskUsed
    $warmSplitDRequired = [int64]($sourceActive - $warmCandidate)
    $warmSplitERequired = $warmCandidate
    $warmSplitDMarginRecommended = Get-SignedMargin $dRecommendedBudget $warmSplitDRequired
    $warmSplitDMarginHard = Get-SignedMargin $dHardBudget $warmSplitDRequired
    $warmSplitEMarginRecommended = Get-SignedMargin $eRecommendedBudget $warmSplitERequired
    $warmSplitEMarginHard = Get-SignedMargin $eHardBudget $warmSplitERequired

    $fullPhysicalHardFit = [bool]($fullPhysicalMarginHard -ge 0)
    $activeDOnlyHardFit = [bool]($activeDOnlyMarginHard -ge 0)
    $warmSplitHardFit = [bool]($warmSplitDMarginHard -ge 0 -and $warmSplitEMarginHard -ge 0)
    $warmSplitERecommendedFit = [bool]($warmSplitEMarginRecommended -ge 0)
    $freshRecommendedAdmission = [bool]([string]$plan.fit.coexistence_state -eq 'CURRENT_HOST_CAN_PROVISION_WITH_RECOMMENDED_RESERVE')

    $decision = $null
    $nextGate = $null
    $candidate = 'NONE'
    if ($freshRecommendedAdmission) {
        $decision = 'PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW_NOT_REQUIRED'
        $nextGate = 'PRODUCTION_VHDX_PROVISIONING_PREFLIGHT'
        $candidate = 'CURRENT_HOST_RECOMMENDED_RESERVE'
    }
    elseif ($warmSplitHardFit -and $warmSplitERecommendedFit) {
        $decision = 'PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW_COPY_CONTRACT_REQUIRED'
        $nextGate = 'PRODUCTION_ACTIVE_DATA_WARM_SPLIT_COPY_CONTRACT_PREFLIGHT'
        $candidate = 'ACTIVE_DATA_WITH_WARM_SPLIT'
    }
    else {
        $decision = 'PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW_BLOCKED'
        $nextGate = 'PRODUCTION_STORAGE_COEXISTENCE_REDESIGN'
    }

    Assert-ExactMain 'reserve_review_final'
    Assert-CurrentRuntimeBoundary $protectedVisualProcessed
    $envHashAfter = Get-Sha256 $envPath
    if ($envHashBefore -ne $envHashAfter) { throw '.env changed during reserve review.' }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_storage_reserve_exception_review_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:ReserveReviewReceiptVersion
        decision=$decision
        next_gate=$nextGate
        read_only=$true
        mutation_performed=$false
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        accepted_post_d_refresh=[ordered]@{ path=$refresh.path; sha256=$refresh.sha256; engine_sha=$script:AcceptedPostDRefreshEngineSha }
        fresh_sizing=[ordered]@{ path=$sizingResult.path; sha256=$sizingResult.sha256; decision=[string]$plan.decision; final_capacity_state=[string]$plan.fit.final_capacity_state; coexistence_state=[string]$plan.fit.coexistence_state }
        source=[ordered]@{
            disk_total_bytes=$sourceDiskTotal
            disk_free_bytes=$sourceDiskFree
            disk_used_bytes=$sourceDiskUsed
            active_bytes_on_disk=$sourceActive
            cn_conditional_warm_candidate_bytes=$warmCandidate
            top_level_du_root_allocated_bytes=[int64]$du.root_allocated_bytes
            top_level_du=@($du.top_level)
        }
        budgets=[ordered]@{
            D=[ordered]@{ total_bytes=$dTotal; free_bytes=$dFree; recommended_new_budget_bytes=$dRecommendedBudget; hard_new_budget_bytes=$dHardBudget }
            E=[ordered]@{ total_bytes=$eTotal; free_bytes=$eFree; recommended_new_budget_bytes=$eRecommendedBudget; hard_new_budget_bytes=$eHardBudget }
        }
        candidates=[ordered]@{
            full_physical_to_D=[ordered]@{
                required_d_bytes=$sourceDiskUsed
                d_recommended_margin_bytes=$fullPhysicalMarginRecommended
                d_hard_margin_bytes=$fullPhysicalMarginHard
                hard_fit=$fullPhysicalHardFit
                copy_contract_proven=$false
                vhdx_authorized=$false
            }
            active_only_to_D=[ordered]@{
                required_d_bytes=$sourceActive
                d_recommended_margin_bytes=$activeDOnlyMarginRecommended
                d_hard_margin_bytes=$activeDOnlyMarginHard
                hard_fit=$activeDOnlyHardFit
                copy_contract_proven=$false
                vhdx_authorized=$false
            }
            active_data_with_warm_split=[ordered]@{
                required_d_bytes=$warmSplitDRequired
                required_e_bytes=$warmSplitERequired
                d_recommended_margin_bytes=$warmSplitDMarginRecommended
                d_hard_margin_bytes=$warmSplitDMarginHard
                e_recommended_margin_bytes=$warmSplitEMarginRecommended
                e_hard_margin_bytes=$warmSplitEMarginHard
                hard_fit=$warmSplitHardFit
                e_recommended_fit=$warmSplitERecommendedFit
                copy_contract_proven=$false
                vhdx_authorized=$false
            }
        }
        recommended_candidate=$candidate
        temporary_20_percent_exception_granted=$false
        production_invariant_preserved=$true
        env_unchanged=$true
        constraints=[ordered]@{
            raw_delete_authorized=$false
            clickhouse_cleanup_authorized=$false
            clickhouse_optimize_authorized=$false
            clickhouse_move_authorized=$false
            clickhouse_ttl_authorized=$false
            vhdx_create_authorized=$false
            vhdx_resize_authorized=$false
            vhdx_mount_authorized=$false
            vhdx_delete_authorized=$false
            wsl_mutation_authorized=$false
            accepted_volume_mutation_authorized=$false
            docker_restart_authorized=$false
            docker_prune_authorized=$false
            cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
    }
    $receiptPath = Join-Path $evidenceDir 'production_storage_reserve_exception_review.json'
    $receipt | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    $gib = [math]::Pow(1024,3)
    Write-Host '===== PRODUCTION STORAGE RESERVE EXCEPTION REVIEW RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "recommended_candidate=$candidate"
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'temporary_20_percent_exception_granted=False'
    Write-Host "fresh_coexistence_state=$([string]$plan.fit.coexistence_state)"
    Write-Host "source_disk_used_bytes=$sourceDiskUsed"
    Write-Host "source_active_bytes=$sourceActive"
    Write-Host "source_warm_candidate_bytes=$warmCandidate"
    Write-Host "source_du_root_allocated_bytes=$([int64]$du.root_allocated_bytes)"
    Write-Host "drive_D_recommended_new_budget_bytes=$dRecommendedBudget"
    Write-Host "drive_D_hard_new_budget_bytes=$dHardBudget"
    Write-Host "drive_E_recommended_new_budget_bytes=$eRecommendedBudget"
    Write-Host "full_physical_d_hard_margin_bytes=$fullPhysicalMarginHard"
    Write-Host ("full_physical_d_hard_margin_gib={0:N2}" -f ($fullPhysicalMarginHard / $gib))
    Write-Host "active_only_d_hard_margin_bytes=$activeDOnlyMarginHard"
    Write-Host ("active_only_d_hard_margin_gib={0:N2}" -f ($activeDOnlyMarginHard / $gib))
    Write-Host "warm_split_d_required_bytes=$warmSplitDRequired"
    Write-Host "warm_split_e_required_bytes=$warmSplitERequired"
    Write-Host "warm_split_d_hard_margin_bytes=$warmSplitDMarginHard"
    Write-Host ("warm_split_d_hard_margin_gib={0:N2}" -f ($warmSplitDMarginHard / $gib))
    Write-Host "warm_split_e_recommended_margin_bytes=$warmSplitEMarginRecommended"
    Write-Host "full_physical_hard_fit=$fullPhysicalHardFit"
    Write-Host "active_only_hard_fit=$activeDOnlyHardFit"
    Write-Host "warm_split_hard_fit=$warmSplitHardFit"
    Write-Host "warm_split_e_recommended_fit=$warmSplitERecommendedFit"
    foreach ($entry in @($du.top_level | Select-Object -First 10)) {
        Write-Host "source_top_level_allocated=$($entry.name):$([int64]$entry.allocated_bytes)"
    }
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'accepted_volume_mutation_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW_DONE'

    Assert-ExactMain 'exit'
}
finally { Pop-Location }
