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
    [string]$EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery',
    [string]$FRecoveryRoot = 'F:\MarkOrbitData\recovery',
    [string]$ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx',
    [ValidateRange(0, 50)]
    [double]$CopySafetyMarginPercent = 10,
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedPostDRefreshEngineSha = 'a18e51a42bee13b9062ad271fd378840a8119d7f'
$script:AcceptedPostDRefreshReceiptVersion = 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_V1'
$script:LayoutReplanReceiptVersion = 'PRODUCTION_STORAGE_LAYOUT_REPLAN_V1'
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
        'Get-OptionalPropertyValue','Get-OptionalArrayProperty','Get-DotEnvValues','Get-DriveSnapshot',
        'Get-ProductionClickHouseHealth','Assert-AcceptedProductionMount','Assert-RawConsumersStopped',
        'Get-AllContainerMounts','Get-ComposeBindMounts','Assert-ComposeRawBindings'
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

function Get-RequiredCapacityBytes([int64]$PayloadBytes, [double]$FreePercent) {
    if ($PayloadBytes -lt 0) { throw 'PayloadBytes must be non-negative.' }
    if ($FreePercent -le 0 -or $FreePercent -ge 100) { throw 'FreePercent must be between 0 and 100.' }
    if ($PayloadBytes -eq 0) { return [int64]0 }
    return [int64][math]::Ceiling([double]$PayloadBytes / (1.0 - ($FreePercent / 100.0)))
}

function Add-CopySafetyMargin([int64]$PayloadBytes) {
    if ($PayloadBytes -lt 0) { throw 'PayloadBytes must be non-negative.' }
    return [int64][math]::Ceiling([double]$PayloadBytes * (1.0 + ($CopySafetyMarginPercent / 100.0)))
}

function Get-SignedMargin([int64]$BudgetBytes, [int64]$RequiredBytes) {
    return [int64]($BudgetBytes - $RequiredBytes)
}

function Invoke-WorkerJson([string[]]$PythonArgs, [string]$Label) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(docker compose run --rm --no-deps -T `
            --volume "${repoRoot}\app:/app/app:ro" `
            worker python @PythonArgs)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $json = ($lines | ForEach-Object { $_.ToString() }) -join "`n"
    if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
    if (-not $json.Trim()) { throw "$Label produced no JSON." }
    try { return $json | ConvertFrom-Json }
    catch { throw "$Label produced invalid JSON: $($_.Exception.Message)" }
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
        throw 'Post-D refresh provenance changed outside the storage-layout-replan tooling boundary.'
    }
}

function Resolve-AcceptedPostDRefreshReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedPostDRefreshReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted post-D refresh receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedPostDRefreshReceiptVersion) { throw 'Unexpected post-D refresh receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedPostDRefreshEngineSha) { throw 'Post-D refresh receipt engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_BLOCKED') { throw 'Post-D refresh receipt is not the accepted blocked state.' }
    if ([string]$receipt.next_gate -ne 'PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW') { throw 'Post-D refresh next gate changed.' }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { throw 'Post-D refresh receipt lost read-only contract.' }
    if ([string]$receipt.fresh_sizing_decision -ne 'PRODUCTION_HOT_WARM_SIZING_PLAN_READY') { throw 'Post-D refresh sizing was not ready.' }
    if ([string]$receipt.final_capacity_state -ne 'RECOMMENDED_30_PERCENT_PLAN_FITS') { throw 'Final 30-percent architecture fit is no longer accepted.' }
    if ([string]$receipt.coexistence_state -ne 'CURRENT_HOST_HARD_FLOOR_ONLY') { throw 'Expected hard-floor-only coexistence state changed.' }
    if ([bool]$receipt.recommended_30_percent_admission) { throw 'Receipt unexpectedly admitted recommended coexistence.' }
    return [ordered]@{ path=$path; sha256=(Get-Sha256 $path); receipt=$receipt }
}

function Resolve-AcceptedSizingPlan([object]$RefreshReceipt) {
    $path = [System.IO.Path]::GetFullPath([string]$RefreshReceipt.fresh_sizing_plan_path)
    $plan = Read-JsonFile $path 'Accepted fresh sizing plan'
    $sha = Get-Sha256 $path
    if ($sha -ne ([string]$RefreshReceipt.fresh_sizing_plan_sha256).Trim().ToLowerInvariant()) { throw 'Accepted fresh sizing plan SHA changed.' }
    if ([string]$plan.plan_version -ne 'PRODUCTION_HOT_WARM_SIZING_PLAN_V1' -or -not [bool]$plan.read_only) { throw 'Accepted fresh sizing plan contract changed.' }
    if ([string]$plan.engine_sha -ne $script:AcceptedPostDRefreshEngineSha) { throw 'Accepted fresh sizing plan engine SHA changed.' }
    if (-not [bool]$plan.production_invariant_preserved -or -not [bool]$plan.env_unchanged) { throw 'Accepted fresh sizing lost production/.env invariant.' }
    return [ordered]@{ path=$path; sha256=$sha; plan=$plan }
}

function Get-DirectoryInventoryNoFollow([string]$Root) {
    $normalized = Normalize-HostPath $Root
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized -PathType Container)) {
        return [ordered]@{ exists=$false; root=$normalized; file_count=[int64]0; logical_bytes=[int64]0; vhdx_count=[int64]0; reparse_count=[int64]0; vhdx=@() }
    }
    $rootAttributes = [System.IO.File]::GetAttributes($normalized)
    if (($rootAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Inventory root is a reparse point: $normalized" }
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($normalized)
    $fileCount = [int64]0
    $logicalBytes = [int64]0
    $reparseCount = [int64]0
    $vhdx = @()
    while ($stack.Count -gt 0) {
        $directory = $stack.Pop()
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
            $attributes = [System.IO.File]::GetAttributes($entry)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                $reparseCount++
                continue
            }
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $stack.Push($entry)
                continue
            }
            $info = New-Object System.IO.FileInfo($entry)
            $fileCount++
            $logicalBytes += [int64]$info.Length
            if ($info.Extension.Equals('.vhdx', [System.StringComparison]::OrdinalIgnoreCase)) {
                $vhdx += [pscustomobject]@{ path=$info.FullName; length=[int64]$info.Length }
            }
        }
    }
    return [ordered]@{
        exists=$true
        root=$normalized
        file_count=$fileCount
        logical_bytes=$logicalBytes
        vhdx_count=[int64]$vhdx.Count
        reparse_count=$reparseCount
        vhdx=@($vhdx | Sort-Object path)
    }
}

function Get-WslBasePaths {
    $result = @()
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction Stop)) {
        $properties = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction Stop
        $basePath = [Environment]::ExpandEnvironmentVariables([string]$properties.BasePath)
        $normalized = Normalize-HostPath $basePath
        if ($normalized) {
            $result += [pscustomobject]@{ distribution=[string]$properties.DistributionName; base_path=$normalized }
        }
    }
    return @($result)
}

function Get-BackupReferenceInventory([string]$BackupRoot, [object[]]$ContainerMounts, [object[]]$ComposeBinds, [hashtable]$EnvValues) {
    $references = @()
    foreach ($mount in @($ContainerMounts)) {
        if ($mount.normalized_source -and (Test-PathsOverlap $BackupRoot $mount.normalized_source)) {
            $references += [pscustomobject]@{ source='docker_container'; identity=[string]$mount.container_name; path=[string]$mount.normalized_source }
        }
    }
    foreach ($bind in @($ComposeBinds)) {
        if ($bind.normalized_source -and (Test-PathsOverlap $BackupRoot $bind.normalized_source)) {
            $references += [pscustomobject]@{ source='docker_compose'; identity=[string]$bind.service; path=[string]$bind.normalized_source }
        }
    }
    foreach ($pair in @($EnvValues.GetEnumerator())) {
        $normalized = Normalize-HostPath ([string]$pair.Value)
        if ($normalized -and (Test-PathsOverlap $BackupRoot $normalized)) {
            $references += [pscustomobject]@{ source='dotenv'; identity=[string]$pair.Key; path=$normalized }
        }
    }
    foreach ($distro in @(Get-WslBasePaths)) {
        if (Test-PathsOverlap $BackupRoot $distro.base_path) {
            $references += [pscustomobject]@{ source='wsl_lxss'; identity=[string]$distro.distribution; path=[string]$distro.base_path }
        }
    }
    return @($references)
}

function Get-UserArchitectureTier([string]$TableName) {
    if ($TableName -eq 'cn_observed_event' -or $TableName.EndsWith('_event', [System.StringComparison]::OrdinalIgnoreCase)) {
        return 'WARM_EVENT_HISTORY'
    }
    if ($TableName.StartsWith('cn_goods_', [System.StringComparison]::OrdinalIgnoreCase)) {
        return 'WARM_GOODS_CATEGORY'
    }
    if ($TableName.StartsWith('cn_', [System.StringComparison]::OrdinalIgnoreCase)) {
        return 'HOT_CURRENT_SERVING_CONSERVATIVE'
    }
    return 'OUT_OF_SCOPE'
}

function Get-CnLayoutScenario([object[]]$Tables) {
    $entries = @()
    $hotBytes = [int64]0
    $warmBytes = [int64]0
    $hotRows = [int64]0
    $warmRows = [int64]0
    foreach ($table in @($Tables | Where-Object { ([string]$_.table).StartsWith('cn_') })) {
        $name = [string]$table.table
        $tier = Get-UserArchitectureTier $name
        $bytes = [int64]$table.bytes_on_disk
        $rows = [int64]$table.rows_from_parts
        if ($tier.StartsWith('WARM_')) {
            $warmBytes += $bytes
            $warmRows += $rows
        }
        else {
            $hotBytes += $bytes
            $hotRows += $rows
        }
        $entries += [pscustomobject]@{
            table=$name
            tier=$tier
            current_placement_contract=[string]$table.placement_contract
            bytes_on_disk=$bytes
            rows_from_parts=$rows
        }
    }
    return [ordered]@{
        tables=@($entries | Sort-Object bytes_on_disk -Descending)
        hot_bytes=$hotBytes
        warm_bytes=$warmBytes
        hot_rows=$hotRows
        warm_rows=$warmRows
        total_bytes=[int64]($hotBytes + $warmBytes)
        total_rows=[int64]($hotRows + $warmRows)
    }
}

function Invoke-ContractFixture {
    foreach ($name in @('Assert-ExactMain','Normalize-HostPath','Get-DriveSnapshot','Get-ProductionClickHouseHealth','Assert-AcceptedProductionMount','Assert-RawConsumersStopped')) {
        if ($null -eq (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Imported helper missing: $name" }
    }
    if ((Get-UserArchitectureTier 'cn_goods_item_current') -ne 'WARM_GOODS_CATEGORY') { throw 'Goods Warm classification contract failed.' }
    if ((Get-UserArchitectureTier 'cn_observed_event') -ne 'WARM_EVENT_HISTORY') { throw 'Observed-event Warm classification contract failed.' }
    if ((Get-UserArchitectureTier 'cn_goods_scope_event') -ne 'WARM_EVENT_HISTORY') { throw 'Event precedence over goods classification failed.' }
    if ((Get-UserArchitectureTier 'cn_case_current') -ne 'HOT_CURRENT_SERVING_CONSERVATIVE') { throw 'Current-serving Hot classification contract failed.' }
    if ((Get-NewAllocationBudget 1000 700 30) -ne 400) { throw 'Recommended budget math contract failed.' }
    if ((Get-RequiredCapacityBytes 700 30) -ne 1000) { throw 'Recommended virtual capacity math contract failed.' }
    if ($script:AllowedReserveReviewToolingFiles.Count -ne 3) { throw 'Storage-layout tooling provenance count changed.' }
    Write-Host 'PRODUCTION_STORAGE_LAYOUT_REPLAN_PS51_CONTRACT_PASS'
}

try {
    Import-AcceptedPreflightHelpers

    if ($ContractOnly) {
        Invoke-ContractFixture
        return
    }

    Write-Host '===== PRODUCTION STORAGE LAYOUT REPLAN ====='
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'e_backup_delete_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
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

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Storage layout replan must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-PostRefreshProvenance

    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Storage layout replan requires elevated Administrator PowerShell.' }

    $legacyRaw = Normalize-HostPath $LegacyRawRoot
    $rawTarget = Normalize-HostPath $RawTargetRoot
    $eBackup = Normalize-HostPath $EBackupRoot
    $fRecovery = Normalize-HostPath $FRecoveryRoot
    $expectedRecoveryVhdx = Normalize-HostPath $ExpectedFRecoveryVhdx
    $protectedVisualProcessed = Normalize-HostPath (Join-Path $legacyRaw 'visual_processed')
    if (-not $legacyRaw.Equals('D:\yoomarks\markorbit-data-engine\raw_data', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyRawRoot changed.' }
    if (-not $rawTarget.Equals('F:\MarkOrbitData\raw', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RawTargetRoot changed.' }
    if (-not $eBackup.Equals('E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'EBackupRoot changed.' }
    if (-not $fRecovery.Equals('F:\MarkOrbitData\recovery', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'FRecoveryRoot changed.' }
    if (-not $expectedRecoveryVhdx.Equals('F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'ExpectedFRecoveryVhdx changed.' }

    $refresh = Resolve-AcceptedPostDRefreshReceipt
    $acceptedSizing = Resolve-AcceptedSizingPlan $refresh.receipt
    $plan = $acceptedSizing.plan
    Write-Host "accepted_post_d_refresh_receipt=$($refresh.path)"
    Write-Host "accepted_post_d_refresh_receipt_sha256=$($refresh.sha256)"
    Write-Host "accepted_sizing_plan=$($acceptedSizing.path)"
    Write-Host "accepted_sizing_plan_sha256=$($acceptedSizing.sha256)"

    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env missing.' }
    $envHashBefore = Get-Sha256 $envPath
    $envValues = Get-DotEnvValues @(Get-Content -LiteralPath $envPath -Encoding UTF8)

    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_layout_replan_before=$([bool]$productionBefore.ready)"
    if (-not [bool]$productionBefore.ready) { throw 'Production ClickHouse must be healthy before layout replan.' }
    Assert-AcceptedProductionMount $productionBefore.container_id
    $composeBinds = @(Get-ComposeBindMounts)
    Assert-ComposeRawBindings $composeBinds $protectedVisualProcessed
    $containerMounts = @(Get-AllContainerMounts)

    Write-Host 'layout_replan_stage=e_backup_and_recovery_inventory'
    $eInventory = Get-DirectoryInventoryNoFollow $eBackup
    $fInventory = Get-DirectoryInventoryNoFollow $fRecovery
    $backupReferences = @(Get-BackupReferenceInventory $eBackup $containerMounts $composeBinds $envValues)
    $recoveryVhdxReady = [bool](Test-Path -LiteralPath $expectedRecoveryVhdx -PathType Leaf)
    $recoveryVhdxBytes = if ($recoveryVhdxReady) { [int64](Get-Item -LiteralPath $expectedRecoveryVhdx).Length } else { [int64]0 }
    $eBackupTechnicalReclaimCandidate = [bool](
        [bool]$eInventory.exists -and
        [int64]$eInventory.file_count -gt 0 -and
        [int64]$eInventory.reparse_count -eq 0 -and
        $backupReferences.Count -eq 0 -and
        [bool]$fInventory.exists -and
        [int64]$fInventory.vhdx_count -gt 0 -and
        $recoveryVhdxReady -and
        $recoveryVhdxBytes -gt 0
    )
    Write-Host "e_backup_exists=$([bool]$eInventory.exists)"
    Write-Host "e_backup_file_count=$([int64]$eInventory.file_count)"
    Write-Host "e_backup_logical_bytes=$([int64]$eInventory.logical_bytes)"
    Write-Host "e_backup_vhdx_count=$([int64]$eInventory.vhdx_count)"
    Write-Host "e_backup_reparse_count=$([int64]$eInventory.reparse_count)"
    Write-Host "e_backup_reference_count=$($backupReferences.Count)"
    Write-Host "f_recovery_exists=$([bool]$fInventory.exists)"
    Write-Host "f_recovery_vhdx_count=$([int64]$fInventory.vhdx_count)"
    Write-Host "expected_f_recovery_vhdx_ready=$recoveryVhdxReady"
    Write-Host "expected_f_recovery_vhdx_bytes=$recoveryVhdxBytes"
    Write-Host "e_backup_technical_reclaim_candidate=$eBackupTechnicalReclaimCandidate"
    Write-Host 'e_backup_duplicate_identity_proven=False'
    Write-Host 'e_backup_delete_authorized=False'

    Write-Host 'layout_replan_stage=cn_user_architecture_scenario'
    $capacityProfile = Invoke-WorkerJson @('-m','app.cn.capacity_profile') 'CN capacity profile'
    if ([string]$capacityProfile.profile_version -ne 'CN_HOT_WARM_CAPACITY_PROFILE_V1' -or
        -not [bool]$capacityProfile.read_only -or [bool]$capacityProfile.full_corpus_scan -or [bool]$capacityProfile.mutation_performed) {
        throw 'CN capacity profile lost read-only metadata contract.'
    }
    $scenario = Get-CnLayoutScenario @($capacityProfile.tables)
    foreach ($entry in @($scenario.tables)) {
        Write-Host "cn_layout table=$($entry.table) tier=$($entry.tier) bytes=$([int64]$entry.bytes_on_disk) rows=$([int64]$entry.rows_from_parts) current_contract=$($entry.current_placement_contract)"
    }
    Write-Host "cn_layout_hot_bytes=$([int64]$scenario.hot_bytes)"
    Write-Host "cn_layout_warm_bytes=$([int64]$scenario.warm_bytes)"
    Write-Host "cn_layout_total_bytes=$([int64]$scenario.total_bytes)"

    $usApplicationHotPayload = [int64]$plan.us_application.projected_total_application_hot_payload_bytes
    $globalHotPayload = [int64]$plan.current_payload.global_other_active_bytes
    $dHotPayload = [int64]([int64]$scenario.hot_bytes + $usApplicationHotPayload + $globalHotPayload)
    $eWarmPayload = [int64]$scenario.warm_bytes
    $dHotPhysicalRequired = Add-CopySafetyMargin $dHotPayload
    $eWarmPhysicalRequired = Add-CopySafetyMargin $eWarmPayload

    $d = Get-DriveSnapshot 'D'
    $e = Get-DriveSnapshot 'E'
    $f = Get-DriveSnapshot 'F'
    $dRecommendedBudget = Get-NewAllocationBudget ([int64]$d.total_bytes) ([int64]$d.free_bytes) 30
    $dHardBudget = Get-NewAllocationBudget ([int64]$d.total_bytes) ([int64]$d.free_bytes) 20
    $eRecommendedBudget = Get-NewAllocationBudget ([int64]$e.total_bytes) ([int64]$e.free_bytes) 30
    $eHardBudget = Get-NewAllocationBudget ([int64]$e.total_bytes) ([int64]$e.free_bytes) 20
    $eProjectedFreeAfterBackup = [int64][math]::Min([double][int64]$e.total_bytes, [double]([int64]$e.free_bytes + [int64]$eInventory.logical_bytes))
    $eProjectedRecommendedBudget = Get-NewAllocationBudget ([int64]$e.total_bytes) $eProjectedFreeAfterBackup 30
    $eProjectedHardBudget = Get-NewAllocationBudget ([int64]$e.total_bytes) $eProjectedFreeAfterBackup 20

    $dRecommendedMargin = Get-SignedMargin $dRecommendedBudget $dHotPhysicalRequired
    $dHardMargin = Get-SignedMargin $dHardBudget $dHotPhysicalRequired
    $eCurrentRecommendedMargin = Get-SignedMargin $eRecommendedBudget $eWarmPhysicalRequired
    $eCurrentHardMargin = Get-SignedMargin $eHardBudget $eWarmPhysicalRequired
    $eProjectedRecommendedMargin = Get-SignedMargin $eProjectedRecommendedBudget $eWarmPhysicalRequired
    $eProjectedHardMargin = Get-SignedMargin $eProjectedHardBudget $eWarmPhysicalRequired

    $dRecommendedFit = [bool]($dRecommendedMargin -ge 0)
    $dHardFit = [bool]($dHardMargin -ge 0)
    $eCurrentRecommendedFit = [bool]($eCurrentRecommendedMargin -ge 0)
    $eCurrentHardFit = [bool]($eCurrentHardMargin -ge 0)
    $eProjectedRecommendedFit = [bool]($eProjectedRecommendedMargin -ge 0)
    $eProjectedHardFit = [bool]($eProjectedHardMargin -ge 0)

    $recommendedHotCnCapacity = Get-RequiredCapacityBytes ([int64]$scenario.hot_bytes) 30
    $recommendedHotUsCapacity = Get-RequiredCapacityBytes $usApplicationHotPayload 30
    $recommendedHotGlobalCapacity = Get-RequiredCapacityBytes $globalHotPayload 30
    $recommendedWarmCapacity = Get-RequiredCapacityBytes $eWarmPayload 30

    Write-Host "copy_safety_margin_percent=$CopySafetyMarginPercent"
    Write-Host "us_application_hot_payload_bytes=$usApplicationHotPayload"
    Write-Host "global_hot_payload_bytes=$globalHotPayload"
    Write-Host "scenario_d_hot_payload_bytes=$dHotPayload"
    Write-Host "scenario_e_warm_payload_bytes=$eWarmPayload"
    Write-Host "scenario_d_hot_physical_required_bytes=$dHotPhysicalRequired"
    Write-Host "scenario_e_warm_physical_required_bytes=$eWarmPhysicalRequired"
    Write-Host "drive_D_recommended_new_budget_bytes=$dRecommendedBudget"
    Write-Host "drive_D_hard_new_budget_bytes=$dHardBudget"
    Write-Host "drive_E_recommended_new_budget_bytes=$eRecommendedBudget"
    Write-Host "drive_E_hard_new_budget_bytes=$eHardBudget"
    Write-Host "drive_E_projected_free_after_backup_logical_reclaim_bytes=$eProjectedFreeAfterBackup"
    Write-Host 'drive_E_backup_reclaim_projection_is_not_delete_authority=True'
    Write-Host "scenario_d_recommended_margin_bytes=$dRecommendedMargin"
    Write-Host "scenario_d_hard_margin_bytes=$dHardMargin"
    Write-Host "scenario_e_current_recommended_margin_bytes=$eCurrentRecommendedMargin"
    Write-Host "scenario_e_current_hard_margin_bytes=$eCurrentHardMargin"
    Write-Host "scenario_e_projected_recommended_margin_bytes=$eProjectedRecommendedMargin"
    Write-Host "scenario_e_projected_hard_margin_bytes=$eProjectedHardMargin"
    Write-Host "scenario_d_recommended_fit=$dRecommendedFit"
    Write-Host "scenario_d_hard_fit=$dHardFit"
    Write-Host "scenario_e_current_recommended_fit=$eCurrentRecommendedFit"
    Write-Host "scenario_e_current_hard_fit=$eCurrentHardFit"
    Write-Host "scenario_e_projected_recommended_fit=$eProjectedRecommendedFit"
    Write-Host "scenario_e_projected_hard_fit=$eProjectedHardFit"

    $decision = 'PRODUCTION_STORAGE_LAYOUT_REPLAN_BLOCKED'
    $nextGate = 'PRODUCTION_STORAGE_COEXISTENCE_REDESIGN'
    if (-not $eBackupTechnicalReclaimCandidate) {
        $decision = 'PRODUCTION_STORAGE_LAYOUT_REPLAN_BACKUP_REVIEW_REQUIRED'
        $nextGate = 'PRODUCTION_E_BACKUP_PROVENANCE_REVIEW'
    }
    elseif (-not $dHardFit -or -not $eProjectedHardFit) {
        $decision = 'PRODUCTION_STORAGE_LAYOUT_REPLAN_CAPACITY_BLOCKED'
        $nextGate = 'PRODUCTION_STORAGE_COEXISTENCE_REDESIGN'
    }
    elseif ($dRecommendedFit -and $eProjectedRecommendedFit) {
        $decision = 'PRODUCTION_STORAGE_LAYOUT_REPLAN_READY'
        $nextGate = 'PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT'
    }
    else {
        $decision = 'PRODUCTION_STORAGE_LAYOUT_REPLAN_HARD_FLOOR_ONLY'
        $nextGate = 'PRODUCTION_CN_WARM_SCOPE_EXPANSION_REVIEW'
    }

    Assert-RawConsumersStopped
    $productionAfter = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_layout_replan_final=$([bool]$productionAfter.ready)"
    if (-not [bool]$productionAfter.ready) { throw 'Production ClickHouse must remain healthy after layout replan.' }
    Assert-AcceptedProductionMount $productionAfter.container_id
    $envHashAfter = Get-Sha256 $envPath
    $envUnchanged = [bool]($envHashBefore -eq $envHashAfter)
    if (-not $envUnchanged) { throw '.env changed during read-only layout replan.' }
    Assert-ExactMain 'exit'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_storage_layout_replan_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:LayoutReplanReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision=$decision
        next_gate=$nextGate
        read_only=$true
        mutation_performed=$false
        accepted_post_d_refresh_receipt_path=$refresh.path
        accepted_post_d_refresh_receipt_sha256=$refresh.sha256
        accepted_sizing_plan_path=$acceptedSizing.path
        accepted_sizing_plan_sha256=$acceptedSizing.sha256
        e_backup=[ordered]@{
            root=$eBackup
            exists=[bool]$eInventory.exists
            file_count=[int64]$eInventory.file_count
            logical_bytes=[int64]$eInventory.logical_bytes
            vhdx_count=[int64]$eInventory.vhdx_count
            reparse_count=[int64]$eInventory.reparse_count
            reference_count=[int]$backupReferences.Count
            references=@($backupReferences)
            technical_reclaim_candidate=$eBackupTechnicalReclaimCandidate
            duplicate_identity_proven=$false
            delete_authorized=$false
        }
        f_recovery=[ordered]@{
            root=$fRecovery
            exists=[bool]$fInventory.exists
            file_count=[int64]$fInventory.file_count
            logical_bytes=[int64]$fInventory.logical_bytes
            vhdx_count=[int64]$fInventory.vhdx_count
            expected_vhdx=$expectedRecoveryVhdx
            expected_vhdx_ready=$recoveryVhdxReady
            expected_vhdx_bytes=$recoveryVhdxBytes
        }
        cn_user_architecture_scenario=[ordered]@{
            rule='cn_goods_* and cn_observed_event/all *_event -> E Warm; other cn_* -> D Hot conservative'
            tables=@($scenario.tables)
            hot_bytes=[int64]$scenario.hot_bytes
            warm_bytes=[int64]$scenario.warm_bytes
            total_bytes=[int64]$scenario.total_bytes
            hot_rows=[int64]$scenario.hot_rows
            warm_rows=[int64]$scenario.warm_rows
            move_authorized=$false
        }
        copy_model=[ordered]@{
            safety_margin_percent=$CopySafetyMarginPercent
            us_application_hot_payload_bytes=$usApplicationHotPayload
            global_hot_payload_bytes=$globalHotPayload
            d_hot_payload_bytes=$dHotPayload
            e_warm_payload_bytes=$eWarmPayload
            d_hot_physical_required_bytes=$dHotPhysicalRequired
            e_warm_physical_required_bytes=$eWarmPhysicalRequired
            recommended_virtual_quotas=[ordered]@{
                hot_cn_capacity_bytes=$recommendedHotCnCapacity
                hot_us_application_capacity_bytes=$recommendedHotUsCapacity
                hot_global_capacity_bytes=$recommendedHotGlobalCapacity
                warm_capacity_bytes=$recommendedWarmCapacity
            }
        }
        drives=[ordered]@{
            D=[ordered]@{ total_bytes=[int64]$d.total_bytes; free_bytes=[int64]$d.free_bytes; recommended_new_budget_bytes=$dRecommendedBudget; hard_new_budget_bytes=$dHardBudget; recommended_margin_bytes=$dRecommendedMargin; hard_margin_bytes=$dHardMargin; recommended_fit=$dRecommendedFit; hard_fit=$dHardFit }
            E=[ordered]@{ total_bytes=[int64]$e.total_bytes; free_bytes=[int64]$e.free_bytes; recommended_new_budget_bytes=$eRecommendedBudget; hard_new_budget_bytes=$eHardBudget; current_recommended_margin_bytes=$eCurrentRecommendedMargin; current_hard_margin_bytes=$eCurrentHardMargin; current_recommended_fit=$eCurrentRecommendedFit; current_hard_fit=$eCurrentHardFit; projected_free_after_backup_logical_reclaim_bytes=$eProjectedFreeAfterBackup; projected_recommended_margin_bytes=$eProjectedRecommendedMargin; projected_hard_margin_bytes=$eProjectedHardMargin; projected_recommended_fit=$eProjectedRecommendedFit; projected_hard_fit=$eProjectedHardFit; projection_is_not_delete_authority=$true }
            F=[ordered]@{ total_bytes=[int64]$f.total_bytes; free_bytes=[int64]$f.free_bytes }
        }
        production_invariant_preserved=[bool]($productionBefore.ready -and $productionAfter.ready)
        env_unchanged=$envUnchanged
        constraints=[ordered]@{
            e_backup_delete_authorized=$false
            cn_warm_move_authorized=$false
            raw_delete_authorized=$false
            vhdx_create_authorized=$false
            vhdx_resize_authorized=$false
            vhdx_mount_authorized=$false
            accepted_volume_mutation_authorized=$false
            docker_restart_authorized=$false
            docker_prune_authorized=$false
            wsl_mutation_authorized=$false
            clickhouse_mutation_authorized=$false
            cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
    }
    $receiptPath = Join-Path $evidenceDir 'production_storage_layout_replan.json'
    $receipt | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== PRODUCTION STORAGE LAYOUT REPLAN RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host "e_backup_technical_reclaim_candidate=$eBackupTechnicalReclaimCandidate"
    Write-Host 'e_backup_delete_authorized=False'
    Write-Host "cn_user_architecture_hot_bytes=$([int64]$scenario.hot_bytes)"
    Write-Host "cn_user_architecture_warm_bytes=$([int64]$scenario.warm_bytes)"
    Write-Host "scenario_d_recommended_fit=$dRecommendedFit"
    Write-Host "scenario_d_hard_fit=$dHardFit"
    Write-Host "scenario_e_current_recommended_fit=$eCurrentRecommendedFit"
    Write-Host "scenario_e_projected_recommended_fit=$eProjectedRecommendedFit"
    Write-Host "recommended_hot_cn_capacity_bytes=$recommendedHotCnCapacity"
    Write-Host "recommended_hot_us_application_capacity_bytes=$recommendedHotUsCapacity"
    Write-Host "recommended_hot_global_capacity_bytes=$recommendedHotGlobalCapacity"
    Write-Host "recommended_warm_capacity_bytes=$recommendedWarmCapacity"
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_STORAGE_LAYOUT_REPLAN_DONE'
}
finally { Pop-Location }
