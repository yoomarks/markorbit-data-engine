[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedApplyReceiptPath = '',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyRawRoot = 'D:\yoomarks\markorbit-data-engine\raw_data',
    [string]$RawTargetRoot = 'F:\MarkOrbitData\raw',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedApplyEngineSha = 'ff2f6d1f35f69d865d31b6e38f1549c2382577d8'
$script:AcceptedAuthorityEngineSha = '74cc3379fc7ff81f29a9235b7c55a0ffda2f4090'
$script:AcceptedAuthorityManifestSha256 = '6cd4399aaaf47aab3c5dde6dfd87dc7a29be676ce0d3da93d3d6e493f2f35253'
$script:AcceptedManifestFileCount = [int64]1146
$script:AcceptedManifestBytes = [int64]57920246250
$script:ApplyReceiptVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_APPLY_V1'
$script:AuthorityJournalVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_JOURNAL_V1'
$script:RefreshReceiptVersion = 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_V1'
$script:AllowedPostApplyToolingFiles = @(
    'scripts/preflight-production-rebalance-post-d-reclaim-refresh.ps1',
    'tests/test_production_rebalance_post_d_reclaim_refresh_contract.py',
    '.github/workflows/production-rebalance-post-d-reclaim-refresh-runtime.yml'
)

function Import-AcceptedPreflightHelpers {
    $helperScriptPath = Join-Path $PSScriptRoot 'preflight-production-rebalance-phase2-d-full-sha256.ps1'
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($helperScriptPath, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw 'Accepted Phase2D preflight helper source no longer parses.' }
    $names = @(
        'Invoke-NativeText','Assert-ExactMain','Normalize-HostPath','Test-PathContains','Test-PathsOverlap',
        'Get-OptionalPropertyValue','Get-OptionalArrayProperty','Get-DotEnvValues','Get-DriveSnapshot',
        'Get-ProductionClickHouseHealth','Assert-AcceptedProductionMount','Assert-RawConsumersStopped',
        'Get-AllContainerMounts','Get-ComposeBindMounts','Assert-ComposeRawBindings','Assert-NoReparsePoints'
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
        $scriptScopedDefinition = [regex]::Replace($definitionText, $pattern, $replacement, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($scriptScopedDefinition -eq $definitionText) { throw "Unable to scope accepted helper definition: $name" }
        Invoke-Expression $scriptScopedDefinition
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Expand-JsonArrayForPowerShell51([object]$Value) {
    if ($null -eq $Value) { return @() }
    $expanded = @()
    foreach ($item in $Value) { $expanded += $item }
    return @($expanded)
}

function Assert-SafeRelativePath([string]$RelativePath) {
    if ([string]::IsNullOrWhiteSpace($RelativePath)) { throw 'Authority relative path is empty.' }
    if ([System.IO.Path]::IsPathRooted($RelativePath) -or $RelativePath.Contains(':')) { throw "Authority relative path is rooted: $RelativePath" }
    foreach ($segment in @($RelativePath -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($segment) -or $segment -eq '.' -or $segment -eq '..') {
            throw "Authority relative path contains an unsafe segment: $RelativePath"
        }
    }
}

function Read-JsonFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label file missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Get-ProtectedTreeSignature([string]$Root) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { throw 'Protected visual_processed directory is missing.' }
    $normalizedRoot = Normalize-HostPath $Root
    $records = @()
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($normalizedRoot)
    while ($stack.Count -gt 0) {
        $directory = $stack.Pop()
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
            $full = [System.IO.Path]::GetFullPath($entry)
            $attributes = [System.IO.File]::GetAttributes($full)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Protected visual_processed contains a reparse point: $full" }
            $relative = $full.Substring($normalizedRoot.Length).TrimStart('\')
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $records += "D|$relative|$([int64]$attributes)"
                $stack.Push($full)
            }
            else {
                $info = New-Object System.IO.FileInfo($full)
                $records += "F|$relative|$([int64]$info.Length)|$([int64]$info.LastWriteTimeUtc.Ticks)|$([int64]$attributes)"
            }
        }
    }
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes((@($records | Sort-Object) -join "`n"))
        return (($algorithm.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally { $algorithm.Dispose() }
}

function Assert-PostApplyProvenance {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:AcceptedApplyEngineSha,$expected) -AllowFailure
    if ($ancestor['exit_code'] -ne 0) { throw 'Accepted Phase2D Apply engine is not an ancestor of current refresh main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"$($script:AcceptedApplyEngineSha)..$expected")
    $changed = @($diff['lines'] | ForEach-Object { $_.Trim().Replace('\','/') } | Where-Object { $_ })
    $unexpected = @($changed | Where-Object { $script:AllowedPostApplyToolingFiles -notcontains $_ })
    $missing = @($script:AllowedPostApplyToolingFiles | Where-Object { $changed -notcontains $_ })
    Write-Host "apply_to_current_changed_file_count=$($changed.Count)"
    Write-Host "apply_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "apply_to_current_missing_tooling_file_count=$($missing.Count)"
    if ($unexpected.Count -ne 0 -or $missing.Count -ne 0 -or $changed.Count -ne $script:AllowedPostApplyToolingFiles.Count) {
        throw "Post-Apply main drift is not limited to the accepted refresh tooling files. changed=$($changed -join ',')"
    }
}

function Resolve-AcceptedApplyReceipt {
    if ([string]::IsNullOrWhiteSpace($AcceptedApplyReceiptPath)) {
        throw 'Post-D reclaim refresh requires explicit -AcceptedApplyReceiptPath.'
    }
    $full = [System.IO.Path]::GetFullPath($AcceptedApplyReceiptPath)
    $reportsRoot = Normalize-HostPath (Join-Path $repoRoot 'reports')
    if (-not (Test-PathContains $reportsRoot $full)) { throw 'Accepted Apply receipt must remain under repository reports.' }
    $receipt = Read-JsonFile $full 'Phase2D Apply receipt'
    if ([string]$receipt.receipt_version -ne $script:ApplyReceiptVersion) { throw 'Phase2D Apply receipt version changed.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedApplyEngineSha) { throw 'Phase2D Apply receipt engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_GO') { throw 'Phase2D Apply receipt is not GO.' }
    if ([string]$receipt.next_gate -ne 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH') { throw 'Phase2D Apply receipt next gate changed.' }
    if (-not [bool]$receipt.apply_requested -or [bool]$receipt.resume_requested -or -not [bool]$receipt.mutation_performed) { throw 'Phase2D Apply receipt execution mode changed.' }
    if ([string]$receipt.authority_manifest_sha256 -ne $script:AcceptedAuthorityManifestSha256) { throw 'Phase2D Apply authority manifest SHA changed.' }
    if ([int64]$receipt.deleted_file_count -ne $script:AcceptedManifestFileCount -or [int64]$receipt.deleted_bytes -ne $script:AcceptedManifestBytes) { throw 'Phase2D Apply deletion dimensions changed.' }
    if (-not [bool]$receipt.temporary_hard_floor_met -or [bool]$receipt.preferred_30_percent_floor_claimed) { throw 'Phase2D Apply reserve semantics changed.' }
    foreach ($requiredTrue in @('production_invariant_preserved','env_unchanged','protected_visual_processed_unchanged')) {
        if (-not [bool](Get-OptionalPropertyValue $receipt $requiredTrue)) { throw "Phase2D Apply receipt lost invariant: $requiredTrue" }
    }
    foreach ($requiredFalse in @('recursive_legacy_raw_root_delete_performed','accepted_volume_mutation_performed','docker_restart_performed','docker_prune_performed','vhdx_mutation_performed','wsl_mutation_performed','clickhouse_mutation_performed','replay_performed','us_bulk_performed')) {
        if ([bool](Get-OptionalPropertyValue $receipt $requiredFalse)) { throw "Phase2D Apply receipt contains unrelated mutation: $requiredFalse" }
    }
    return [ordered]@{ path=$full; sha256=(Get-Sha256 $full); receipt=$receipt }
}

function Load-GoJournalAndManifest([object]$ApplyReceipt) {
    $journalPath = [System.IO.Path]::GetFullPath([string]$ApplyReceipt.authority_journal_path)
    $reportsRoot = Normalize-HostPath (Join-Path $repoRoot 'reports')
    if (-not (Test-PathContains $reportsRoot $journalPath)) { throw 'GO journal must remain under repository reports.' }
    $journal = Read-JsonFile $journalPath 'Phase2D GO journal'
    if ([string]$journal.journal_version -ne $script:AuthorityJournalVersion) { throw 'GO journal version changed.' }
    if ([string]$journal.engine_sha -ne $script:AcceptedAuthorityEngineSha) { throw 'GO journal authority engine changed.' }
    if ([string]$journal.apply_engine_sha -ne $script:AcceptedApplyEngineSha) { throw 'GO journal Apply engine changed.' }
    if ([string]$journal.state -ne 'GO' -or [string]$journal.phase -ne 'all_authorized_files_deleted_and_verified' -or -not [bool]$journal.mutation_started) { throw 'Phase2D journal is not final GO.' }
    if ([string]$journal.authority_manifest_sha256 -ne $script:AcceptedAuthorityManifestSha256) { throw 'GO journal manifest SHA changed.' }
    if ([int64]$journal.manifest_file_count -ne $script:AcceptedManifestFileCount -or [int64]$journal.manifest_bytes -ne $script:AcceptedManifestBytes) { throw 'GO journal manifest dimensions changed.' }
    if ([int64]$journal.deleted_file_count -ne $script:AcceptedManifestFileCount -or [int64]$journal.deleted_bytes -ne $script:AcceptedManifestBytes) { throw 'GO journal deletion counters changed.' }
    if (-not [string]::IsNullOrWhiteSpace([string]$journal.inflight_relative_path)) { throw 'GO journal still contains inflight path.' }
    $completed = @(Expand-JsonArrayForPowerShell51 (Get-OptionalPropertyValue $journal 'completed_relative_paths'))
    if ($completed.Count -ne $script:AcceptedManifestFileCount) { throw "GO journal completed path count changed: $($completed.Count)" }
    if (-not (Normalize-HostPath ([string]$journal.source_root)).Equals('D:\yoomarks\markorbit-data-engine\raw_data', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'GO journal source root changed.' }
    if (-not (Normalize-HostPath ([string]$journal.target_root)).Equals('F:\MarkOrbitData\raw', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'GO journal target root changed.' }
    if (-not (Normalize-HostPath ([string]$journal.protected_visual_processed)).Equals('D:\yoomarks\markorbit-data-engine\raw_data\visual_processed', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'GO journal protected root changed.' }

    $manifestPath = [System.IO.Path]::GetFullPath([string]$journal.authority_manifest_path)
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Frozen authority manifest is missing.' }
    $manifestSha = Get-Sha256 $manifestPath
    if (-not $manifestSha.Equals($script:AcceptedAuthorityManifestSha256, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Frozen authority manifest SHA changed.' }
    $parsed = Read-JsonFile $manifestPath 'Frozen authority manifest'
    $entries = @(Expand-JsonArrayForPowerShell51 $parsed)
    Write-Host "authority_manifest_json_expanded_count=$($entries.Count)"
    if ($entries.Count -ne $script:AcceptedManifestFileCount) { throw "Frozen authority manifest file count changed: $($entries.Count)" }
    $bytes = [int64]0
    $seen = @{}
    foreach ($entry in $entries) {
        $relative = [string]$entry.relative_path
        Assert-SafeRelativePath $relative
        $key = $relative.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { throw "Duplicate frozen authority path: $relative" }
        $seen[$key] = $true
        $bytes += [int64]$entry.length
    }
    if ($bytes -ne $script:AcceptedManifestBytes) { throw "Frozen authority manifest bytes changed: $bytes" }
    return [ordered]@{ path=$journalPath; sha256=(Get-Sha256 $journalPath); journal=$journal; manifest_path=$manifestPath; manifest_sha256=$manifestSha; entries=@($entries | Sort-Object relative_path) }
}

function Assert-CurrentBindings([object]$Journal) {
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env is required.' }
    if (-not (Get-Sha256 $envPath).Equals(([string]$Journal.env_sha256), [System.StringComparison]::OrdinalIgnoreCase)) { throw '.env changed since frozen authority.' }
    $values = Get-DotEnvValues @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    $target = Normalize-HostPath ([string]$Journal.target_root)
    $protected = Normalize-HostPath ([string]$Journal.protected_visual_processed)
    $raw = if ($values.ContainsKey('RAW_DATA_PATH')) { Normalize-HostPath ([string]$values['RAW_DATA_PATH']) } else { '' }
    $visualRaw = if ($values.ContainsKey('VISUAL_RAW_PATH')) { Normalize-HostPath ([string]$values['VISUAL_RAW_PATH']) } else { $raw }
    $visualProcessed = if ($values.ContainsKey('VISUAL_PROCESSED_PATH')) { Normalize-HostPath ([string]$values['VISUAL_PROCESSED_PATH']) } else { $protected }
    if (-not $raw.Equals($target, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RAW_DATA_PATH no longer points to accepted F Raw.' }
    if (-not $visualRaw.Equals($target, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_RAW_PATH no longer points to accepted F Raw.' }
    if (-not $visualProcessed.Equals($protected, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_PROCESSED_PATH no longer points to protected D subtree.' }

    $compose = @(Get-ComposeBindMounts)
    Assert-ComposeRawBindings $compose $protected
    $containers = @(Get-AllContainerMounts)
    $sourceRoot = Normalize-HostPath ([string]$Journal.source_root)
    $containerRefs = @($containers | Where-Object { $_.normalized_source -and (Test-PathsOverlap $sourceRoot $_.normalized_source) })
    $composeRefs = @($compose | Where-Object { $_.normalized_source -and (Test-PathsOverlap $sourceRoot $_.normalized_source) })
    $unexpectedContainers = @($containerRefs | Where-Object { -not (Test-PathContains $protected $_.normalized_source) })
    $unexpectedCompose = @($composeRefs | Where-Object { -not (Test-PathContains $protected $_.normalized_source) })
    Write-Host "phase2_d_unexpected_container_reference_count=$($unexpectedContainers.Count)"
    Write-Host "phase2_d_unexpected_compose_reference_count=$($unexpectedCompose.Count)"
    if ($unexpectedContainers.Count -ne 0 -or $unexpectedCompose.Count -ne 0) { throw 'D Raw regained references outside protected visual_processed.' }
}

function Assert-PostDeleteAuthorityState([object]$Journal, [object[]]$Entries) {
    $sourceRoot = Normalize-HostPath ([string]$Journal.source_root)
    $targetRoot = Normalize-HostPath ([string]$Journal.target_root)
    $protected = Normalize-HostPath ([string]$Journal.protected_visual_processed)
    foreach ($required in @($sourceRoot,$targetRoot,$protected)) {
        if (-not (Test-Path -LiteralPath $required -PathType Container)) { throw "Required post-reclaim directory missing: $required" }
    }
    Assert-NoReparsePoints $sourceRoot
    Assert-NoReparsePoints $targetRoot
    $index = 0
    foreach ($entry in $Entries) {
        $index++
        $relative = [string]$entry.relative_path
        $source = Normalize-HostPath ([string]$entry.source_path)
        $target = Normalize-HostPath ([string]$entry.target_path)
        $expectedSource = Normalize-HostPath (Join-Path $sourceRoot $relative)
        $expectedTarget = Normalize-HostPath (Join-Path $targetRoot $relative)
        if (-not $source.Equals($expectedSource, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Frozen source path mismatch: $relative" }
        if (-not $target.Equals($expectedTarget, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Frozen target path mismatch: $relative" }
        if (Test-PathContains $protected $source) { throw "Protected visual_processed leaked into frozen authority: $relative" }
        if (Test-Path -LiteralPath $source) { throw "Deleted D authority file reappeared: $relative" }
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "F authority counterpart missing: $relative" }
        $attributes = [System.IO.File]::GetAttributes($target)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or ($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { throw "F authority counterpart is not a normal file: $relative" }
        $info = New-Object System.IO.FileInfo($target)
        if ([int64]$info.Length -ne [int64]$entry.length) { throw "F authority counterpart length changed: $relative" }
        if (($index % 250) -eq 0 -or $index -eq $Entries.Count) { Write-Host "post_reclaim_authority_progress=$index/$($Entries.Count)" }
    }

    $unexpectedSourceFiles = @()
    foreach ($filePath in [System.IO.Directory]::EnumerateFiles($sourceRoot, '*', [System.IO.SearchOption]::AllDirectories)) {
        $full = Normalize-HostPath $filePath
        if (-not (Test-PathContains $protected $full)) { $unexpectedSourceFiles += $full }
    }
    Write-Host "unexpected_d_raw_file_count=$($unexpectedSourceFiles.Count)"
    if ($unexpectedSourceFiles.Count -ne 0) { throw 'D Raw contains files outside protected visual_processed after accepted reclaim.' }

    $signature = Get-ProtectedTreeSignature $protected
    if (-not $signature.Equals(([string]$Journal.protected_tree_signature), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Protected visual_processed changed after accepted reclaim.' }
    return $signature
}

function Assert-ProductionBoundary([object]$Journal, [string]$Phase) {
    Assert-ExactMain $Phase
    if ((Test-Path -LiteralPath $LegacyEHotRoot) -or (Test-Path -LiteralPath $LegacyEHotLogsRoot)) { throw 'Legacy E hot/log roots reappeared.' }
    Assert-RawConsumersStopped
    $production = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_${Phase}=$([bool]$production.ready)"
    if (-not [bool]$production.ready) { throw "Production ClickHouse unhealthy during $Phase." }
    Assert-AcceptedProductionMount $production.container_id
    Assert-CurrentBindings $Journal
}

function Invoke-FreshSizing {
    $runId = '{0}_{1}' -f (Get-Date -Format 'yyyyMMdd_HHmmssfff'), $PID
    $relativeRoot = Join-Path (Join-Path 'reports' '_pdr') $runId
    $absoluteRoot = Join-Path $repoRoot $relativeRoot
    [System.IO.Directory]::CreateDirectory($absoluteRoot) | Out-Null
    Write-Host "post_reclaim_sizing_evidence_root=$absoluteRoot"
    $scriptPath = Join-Path $PSScriptRoot 'plan-production-hot-warm-sizing.ps1'
    $childArgs = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-AcceptedVolume',$AcceptedVolume,
        '-EvidenceRoot',$relativeRoot
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& powershell.exe @childArgs 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    foreach ($line in @($output | ForEach-Object { $_.ToString() })) { Write-Host $line }
    if ($exitCode -ne 0) { throw "Fresh production Hot/Warm sizing exited $exitCode." }
    $dirs = @(Get-ChildItem -LiteralPath $absoluteRoot -Directory -Filter 'production_hot_warm_sizing_*' | Sort-Object LastWriteTime -Descending)
    if ($dirs.Count -ne 1) { throw "Expected exactly one isolated fresh sizing directory; observed $($dirs.Count)." }
    $path = Join-Path $dirs[0].FullName 'production_hot_warm_sizing_plan.json'
    $report = Read-JsonFile $path 'Fresh production Hot/Warm sizing plan'
    return [ordered]@{ path=$path; sha256=(Get-Sha256 $path); report=$report; evidence_root=$absoluteRoot }
}

function Assert-SizingReadOnlyContract([object]$Plan) {
    if ([string]$Plan.plan_version -ne 'PRODUCTION_HOT_WARM_SIZING_PLAN_V1') { throw 'Fresh sizing plan version changed.' }
    if (-not [bool]$Plan.read_only) { throw 'Fresh sizing plan is not read-only.' }
    if ([string]$Plan.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Fresh sizing plan engine SHA changed.' }
    if (-not [bool]$Plan.production_invariant_preserved -or -not [bool]$Plan.env_unchanged) { throw 'Fresh sizing plan lost production/.env invariant.' }
    foreach ($constraint in @('conditional_cn_warm_demotion_authorized','vhdx_create_authorized','vhdx_resize_authorized','vhdx_mount_authorized','live_migration_authorized','source_volume_delete_authorized','raw_delete_authorized','full_cn_replay_authorized','us_package_2_authorized','us_bulk_authorized')) {
        if ([bool](Get-OptionalPropertyValue $Plan.constraints $constraint)) { throw "Fresh sizing unexpectedly authorized: $constraint" }
    }
    foreach ($performed in @('vhdx_create_performed','vhdx_resize_performed','vhdx_mount_performed','vhdx_move_performed','wsl_unmount_performed','wsl_shutdown_performed','docker_restart_performed','docker_prune_performed','production_clickhouse_mutation_performed','accepted_volume_mutation_performed','source_copy_performed','corpus_replay_performed')) {
        if ([bool](Get-OptionalPropertyValue $Plan $performed)) { throw "Fresh sizing unexpectedly performed mutation: $performed" }
    }
    foreach ($letter in @('D','E','F')) {
        $drive = Get-OptionalPropertyValue $Plan.drives $letter
        if ($null -eq $drive -or [int64]$drive.total_bytes -le 0 -or [int64]$drive.free_bytes -le 0) { throw "Fresh sizing drive evidence invalid: $letter" }
    }
}

function Invoke-ContractFixture {
    foreach ($name in @('Assert-ExactMain','Normalize-HostPath','Get-DriveSnapshot','Get-ProductionClickHouseHealth','Assert-AcceptedProductionMount','Assert-RawConsumersStopped','Get-DotEnvValues')) {
        if ($null -eq (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Imported helper missing after scope return: $name" }
    }
    $parsed = '[{"relative_path":"one.bin"},{"relative_path":"two.bin"}]' | ConvertFrom-Json
    $expanded = @(Expand-JsonArrayForPowerShell51 $parsed)
    if ($expanded.Count -ne 2) { throw 'PS5.1 top-level JSON array expansion failed.' }
    Assert-SafeRelativePath 'folder\file.bin'
    $unsafeFailed = $false
    try { Assert-SafeRelativePath '..\escape.bin' } catch { $unsafeFailed = $true }
    if (-not $unsafeFailed) { throw 'Unsafe relative path did not fail closed.' }
    if ($script:AllowedPostApplyToolingFiles.Count -ne 3) { throw 'Post-Apply tooling provenance file count changed.' }
    Write-Host 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_PS51_CONTRACT_PASS'
}

try {
    Import-AcceptedPreflightHelpers

    if ($ContractOnly) {
        Invoke-ContractFixture
        return
    }

    Write-Host '===== PRODUCTION REBALANCE POST-D RECLAIM REFRESH ====='
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'raw_delete_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'vhdx_resize_authorized=False'
    Write-Host 'vhdx_mount_authorized=False'
    Write-Host 'accepted_volume_copy_authorized=False'
    Write-Host 'accepted_volume_move_authorized=False'
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'docker_restart_authorized=False'
    Write-Host 'docker_prune_authorized=False'
    Write-Host 'wsl_attach_authorized=False'
    Write-Host 'wsl_unmount_authorized=False'
    Write-Host 'wsl_shutdown_authorized=False'
    Write-Host 'wsl_unregister_authorized=False'
    Write-Host 'clickhouse_cutover_authorized=False'
    Write-Host 'clickhouse_mutation_authorized=False'
    Write-Host 'cn_replay_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Post-D reclaim refresh must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-PostApplyProvenance

    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Post-D reclaim refresh requires elevated Administrator PowerShell.' }

    if (-not (Normalize-HostPath $LegacyRawRoot).Equals('D:\yoomarks\markorbit-data-engine\raw_data', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyRawRoot changed.' }
    if (-not (Normalize-HostPath $RawTargetRoot).Equals('F:\MarkOrbitData\raw', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RawTargetRoot changed.' }
    if (-not (Normalize-HostPath $LegacyEHotRoot).Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotRoot changed.' }
    if (-not (Normalize-HostPath $LegacyEHotLogsRoot).Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotLogsRoot changed.' }

    $apply = Resolve-AcceptedApplyReceipt
    Write-Host "accepted_phase2d_apply_receipt=$($apply.path)"
    Write-Host "accepted_phase2d_apply_receipt_sha256=$($apply.sha256)"
    $authority = Load-GoJournalAndManifest $apply.receipt
    Write-Host "go_journal_path=$($authority.path)"
    Write-Host "go_journal_sha256=$($authority.sha256)"
    Write-Host "authority_manifest_sha256=$($authority.manifest_sha256)"
    Write-Host "authority_manifest_file_count=$($authority.entries.Count)"

    Assert-ProductionBoundary $authority.journal 'post_reclaim_before'
    $protectedSignatureBefore = Assert-PostDeleteAuthorityState $authority.journal $authority.entries

    $sizingResult = Invoke-FreshSizing
    $plan = $sizingResult.report
    Assert-SizingReadOnlyContract $plan

    Assert-ProductionBoundary $authority.journal 'post_reclaim_final'
    $protectedSignatureAfter = Assert-PostDeleteAuthorityState $authority.journal $authority.entries
    if (-not $protectedSignatureAfter.Equals($protectedSignatureBefore, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Protected visual_processed changed during post-reclaim refresh.' }
    Assert-ExactMain 'exit'

    $planReady = [bool]([string]$plan.decision -eq 'PRODUCTION_HOT_WARM_SIZING_PLAN_READY' -and @($plan.blockers).Count -eq 0)
    $recommendedFinalFits = [bool]([string]$plan.fit.final_capacity_state -eq 'RECOMMENDED_30_PERCENT_PLAN_FITS')
    $recommendedCoexistenceFits = [bool]([string]$plan.fit.coexistence_state -eq 'CURRENT_HOST_CAN_PROVISION_WITH_RECOMMENDED_RESERVE')
    $recommendedAdmission = [bool]($planReady -and $recommendedFinalFits -and $recommendedCoexistenceFits)
    $decision = if ($recommendedAdmission) { 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_READY' } else { 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_BLOCKED' }
    $nextGate = if ($recommendedAdmission) { 'PRODUCTION_VHDX_PROVISIONING_PREFLIGHT' } else { [string]$plan.next_gate }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_post_d_reclaim_refresh_$timestamp")
    [System.IO.Directory]::CreateDirectory($evidenceDir) | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:RefreshReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision=$decision
        next_gate=$nextGate
        read_only=$true
        mutation_performed=$false
        accepted_phase2d_apply_receipt_path=$apply.path
        accepted_phase2d_apply_receipt_sha256=$apply.sha256
        go_journal_path=$authority.path
        go_journal_sha256=$authority.sha256
        authority_manifest_path=$authority.manifest_path
        authority_manifest_sha256=$authority.manifest_sha256
        authority_file_count=[int64]$authority.entries.Count
        authority_bytes=$script:AcceptedManifestBytes
        d_authority_files_remain_absent=$true
        protected_visual_processed_unchanged=$true
        fresh_sizing_plan_path=$sizingResult.path
        fresh_sizing_plan_sha256=$sizingResult.sha256
        fresh_sizing_decision=[string]$plan.decision
        fresh_sizing_next_gate=[string]$plan.next_gate
        final_capacity_state=[string]$plan.fit.final_capacity_state
        coexistence_state=[string]$plan.fit.coexistence_state
        recommended_30_percent_admission=$recommendedAdmission
        drives=[ordered]@{
            D=[ordered]@{ total_bytes=[int64]$plan.drives.D.total_bytes; free_bytes=[int64]$plan.drives.D.free_bytes; recommended_new_budget_bytes=[int64]$plan.drives.D.current_new_allocation_budget_recommended_bytes; hard_new_budget_bytes=[int64]$plan.drives.D.current_new_allocation_budget_hard_bytes }
            E=[ordered]@{ total_bytes=[int64]$plan.drives.E.total_bytes; free_bytes=[int64]$plan.drives.E.free_bytes; recommended_new_budget_bytes=[int64]$plan.drives.E.current_new_allocation_budget_recommended_bytes; hard_new_budget_bytes=[int64]$plan.drives.E.current_new_allocation_budget_hard_bytes }
            F=[ordered]@{ total_bytes=[int64]$plan.drives.F.total_bytes; free_bytes=[int64]$plan.drives.F.free_bytes; role=[string]$plan.drives.F.role }
        }
        source_active=[ordered]@{ rows=[int64]$plan.current_payload.source_active_rows; bytes_on_disk=[int64]$plan.current_payload.source_active_bytes_on_disk }
        target_quotas=$plan.target_quotas
        fit=$plan.fit
        sizing_blockers=@($plan.blockers)
        production_invariant_preserved=$true
        env_unchanged=$true
        constraints=[ordered]@{
            raw_delete_authorized=$false
            vhdx_create_authorized=$false
            vhdx_resize_authorized=$false
            vhdx_mount_authorized=$false
            vhdx_delete_authorized=$false
            accepted_volume_copy_authorized=$false
            accepted_volume_move_authorized=$false
            accepted_volume_delete_authorized=$false
            docker_restart_authorized=$false
            docker_prune_authorized=$false
            wsl_attach_authorized=$false
            wsl_unmount_authorized=$false
            wsl_shutdown_authorized=$false
            wsl_unregister_authorized=$false
            clickhouse_cutover_authorized=$false
            clickhouse_mutation_authorized=$false
            cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
    }
    $receiptPath = Join-Path $evidenceDir 'production_rebalance_post_d_reclaim_refresh.json'
    $receipt | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    $gib = [math]::Pow(1024,3)
    Write-Host '===== PRODUCTION REBALANCE POST-D RECLAIM REFRESH RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host "fresh_sizing_decision=$([string]$plan.decision)"
    Write-Host "final_capacity_state=$([string]$plan.fit.final_capacity_state)"
    Write-Host "coexistence_state=$([string]$plan.fit.coexistence_state)"
    Write-Host "recommended_30_percent_admission=$recommendedAdmission"
    Write-Host "drive_D_total_bytes=$([int64]$plan.drives.D.total_bytes)"
    Write-Host "drive_D_free_bytes=$([int64]$plan.drives.D.free_bytes)"
    Write-Host ("drive_D_free_gib={0:N2}" -f ([int64]$plan.drives.D.free_bytes / $gib))
    Write-Host "drive_E_total_bytes=$([int64]$plan.drives.E.total_bytes)"
    Write-Host "drive_E_free_bytes=$([int64]$plan.drives.E.free_bytes)"
    Write-Host ("drive_E_free_gib={0:N2}" -f ([int64]$plan.drives.E.free_bytes / $gib))
    Write-Host "drive_F_total_bytes=$([int64]$plan.drives.F.total_bytes)"
    Write-Host "drive_F_free_bytes=$([int64]$plan.drives.F.free_bytes)"
    Write-Host ("drive_F_free_gib={0:N2}" -f ([int64]$plan.drives.F.free_bytes / $gib))
    Write-Host "source_active_rows=$([int64]$plan.current_payload.source_active_rows)"
    Write-Host "source_active_bytes_on_disk=$([int64]$plan.current_payload.source_active_bytes_on_disk)"
    Write-Host "recommended_hot_cn_capacity_bytes=$([int64]$plan.target_quotas.recommended.hot_cn_capacity_bytes)"
    Write-Host "recommended_hot_us_application_capacity_bytes=$([int64]$plan.target_quotas.recommended.hot_us_application_capacity_bytes)"
    Write-Host "recommended_hot_global_bootstrap_capacity_bytes=$([int64]$plan.target_quotas.recommended.hot_global_bootstrap_capacity_bytes)"
    Write-Host "recommended_warm_candidate_capacity_bytes=$([int64]$plan.target_quotas.recommended.warm_candidate_capacity_bytes)"
    Write-Host 'raw_delete_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'vhdx_mount_authorized=False'
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH_DONE'
}
finally { Pop-Location }
