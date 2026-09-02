[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AuthorityJournalPath,
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

$script:AcceptedAuthorityEngineSha = '74cc3379fc7ff81f29a9235b7c55a0ffda2f4090'
$script:AcceptedAuthorityManifestSha256 = '6cd4399aaaf47aab3c5dde6dfd87dc7a29be676ce0d3da93d3d6e493f2f35253'
$script:AcceptedManifestFileCount = [int64]1146
$script:AcceptedManifestBytes = [int64]57920246250
$script:AuthorityJournalVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_JOURNAL_V1'
$script:ReceiptVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_DRY_RUN_V1'

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

function Expand-JsonArrayForPowerShell51([object]$ParsedValue) {
    if ($null -eq $ParsedValue) { return @() }
    $expanded = @()
    foreach ($item in $ParsedValue) { $expanded += $item }
    return @($expanded)
}

function Assert-SafeRelativePath([string]$RelativePath) {
    if ([string]::IsNullOrWhiteSpace($RelativePath)) { throw 'Authority relative path is empty.' }
    if ([System.IO.Path]::IsPathRooted($RelativePath) -or $RelativePath.Contains(':')) { throw "Authority relative path is rooted: $RelativePath" }
    foreach ($segment in @($RelativePath -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($segment) -or $segment -eq '.' -or $segment -eq '..') { throw "Authority relative path contains unsafe segment: $RelativePath" }
    }
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
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Protected visual_processed gained a reparse point: $full" }
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

function Load-AuthorityJournal([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $reportsRoot = Normalize-HostPath (Join-Path $repoRoot 'reports')
    if (-not (Test-PathContains $reportsRoot $full)) { throw 'Authority journal must remain under repository reports.' }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw 'Authority journal is missing.' }
    try { $journal = Get-Content -LiteralPath $full -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Authority journal JSON invalid: $($_.Exception.Message)" }
    if ([string]$journal.journal_version -ne $script:AuthorityJournalVersion) { throw 'Authority journal version changed.' }
    if ([string]$journal.engine_sha -ne $script:AcceptedAuthorityEngineSha) { throw 'Authority journal engine SHA changed.' }
    if ([string]$journal.state -ne 'PREPARED' -or [bool]$journal.mutation_started) { throw 'Dry-run requires untouched PREPARED authority journal.' }
    if ([int64]$journal.deleted_file_count -ne 0 -or [int64]$journal.deleted_bytes -ne 0) { throw 'Dry-run authority journal already records deletion.' }
    if (-not [string]::IsNullOrWhiteSpace([string]$journal.inflight_relative_path)) { throw 'Dry-run authority journal unexpectedly has inflight path.' }
    if ([string]$journal.authority_manifest_sha256 -ne $script:AcceptedAuthorityManifestSha256) { throw 'Authority journal manifest SHA changed.' }
    if ([int64]$journal.manifest_file_count -ne $script:AcceptedManifestFileCount -or [int64]$journal.manifest_bytes -ne $script:AcceptedManifestBytes) { throw 'Authority journal manifest dimensions changed.' }
    if ([string]$journal.source_root -ne 'D:\yoomarks\markorbit-data-engine\raw_data') { throw 'Authority journal source root changed.' }
    if ([string]$journal.target_root -ne 'F:\MarkOrbitData\raw') { throw 'Authority journal target root changed.' }
    if ([string]$journal.protected_visual_processed -ne 'D:\yoomarks\markorbit-data-engine\raw_data\visual_processed') { throw 'Authority journal protected root changed.' }
    return [ordered]@{ path=$full; sha256=(Get-Sha256 $full); journal=$journal }
}

function Load-AuthorityManifest([object]$Journal) {
    $path = [System.IO.Path]::GetFullPath([string]$Journal.authority_manifest_path)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Authority manifest is missing.' }
    $sha = Get-Sha256 $path
    if (-not $sha.Equals($script:AcceptedAuthorityManifestSha256, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Authority manifest SHA changed.' }
    try { $parsed = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Authority manifest JSON invalid: $($_.Exception.Message)" }
    $entries = @(Expand-JsonArrayForPowerShell51 $parsed)
    Write-Host "authority_manifest_json_expanded_count=$($entries.Count)"
    if ($entries.Count -ne $script:AcceptedManifestFileCount) { throw "Authority manifest file count changed: $($entries.Count)" }
    $seen = @{}
    $bytes = [int64]0
    foreach ($entry in $entries) {
        $relative = [string]$entry.relative_path
        Assert-SafeRelativePath $relative
        $key = $relative.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { throw "Duplicate authority path: $relative" }
        $seen[$key] = $true
        $source = Normalize-HostPath ([string]$entry.source_path)
        $target = Normalize-HostPath ([string]$entry.target_path)
        $expectedSource = Normalize-HostPath (Join-Path ([string]$Journal.source_root) $relative)
        $expectedTarget = Normalize-HostPath (Join-Path ([string]$Journal.target_root) $relative)
        if (-not $source.Equals($expectedSource, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Authority source path mismatch: $relative" }
        if (-not $target.Equals($expectedTarget, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Authority target path mismatch: $relative" }
        if (Test-PathContains ([string]$Journal.protected_visual_processed) $source) { throw "Protected visual_processed leaked into authority: $relative" }
        $sourceSha = ([string]$entry.source_sha256).Trim().ToLowerInvariant()
        $targetSha = ([string]$entry.target_sha256).Trim().ToLowerInvariant()
        if (-not [bool]$entry.hash_equal -or $sourceSha -notmatch '^[0-9a-f]{64}$' -or $sourceSha -ne $targetSha) { throw "Authority SHA pair invalid: $relative" }
        $bytes += [int64]$entry.length
    }
    if ($bytes -ne $script:AcceptedManifestBytes) { throw "Authority manifest byte total changed: $bytes" }
    return [ordered]@{ path=$path; sha256=$sha; entries=@($entries | Sort-Object relative_path); bytes=$bytes }
}

function Assert-CurrentBindings([object]$Journal) {
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env is required.' }
    if (-not (Get-Sha256 $envPath).Equals(([string]$Journal.env_sha256), [System.StringComparison]::OrdinalIgnoreCase)) { throw '.env changed since PREPARED authority.' }
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
    if ($unexpectedContainers.Count -ne 0 -or $unexpectedCompose.Count -ne 0) { throw 'D Raw has references outside protected visual_processed.' }
}

function Assert-NormalFileExact([object]$Entry, [string]$Side) {
    $path = if ($Side -eq 'D') { [string]$Entry.source_path } else { [string]$Entry.target_path }
    $expectedSha = if ($Side -eq 'D') { ([string]$Entry.source_sha256).Trim().ToLowerInvariant() } else { ([string]$Entry.target_sha256).Trim().ToLowerInvariant() }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "$Side authority file missing: $([string]$Entry.relative_path)" }
    $attributes = [System.IO.File]::GetAttributes($path)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or ($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { throw "$Side authority path is not a normal file: $([string]$Entry.relative_path)" }
    $info = New-Object System.IO.FileInfo($path)
    if ([int64]$info.Length -ne [int64]$Entry.length) { throw "$Side authority length changed: $([string]$Entry.relative_path)" }
    $actualSha = Get-Sha256 $path
    if (-not $actualSha.Equals($expectedSha, [System.StringComparison]::OrdinalIgnoreCase)) { throw "$Side authority SHA changed: $([string]$Entry.relative_path)" }
}

function Assert-GlobalBoundary([object]$Journal, [string]$Phase) {
    Assert-ExactMain $Phase
    if ((Test-Path -LiteralPath $LegacyEHotRoot) -or (Test-Path -LiteralPath $LegacyEHotLogsRoot)) { throw 'Legacy E roots reappeared.' }
    Assert-RawConsumersStopped
    $production = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_${Phase}=$([bool]$production.ready)"
    if (-not [bool]$production.ready) { throw "Production ClickHouse unhealthy during $Phase." }
    Assert-AcceptedProductionMount $production.container_id
    Assert-CurrentBindings $Journal
    $protectedSignature = Get-ProtectedTreeSignature ([string]$Journal.protected_visual_processed)
    if (-not $protectedSignature.Equals(([string]$Journal.protected_tree_signature), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Protected visual_processed changed since PREPARED authority.' }
}

function Invoke-FullShaDryRun([object[]]$Entries) {
    $bytes = [int64]0
    $index = 0
    foreach ($entry in $Entries) {
        $index++
        Assert-NormalFileExact $entry 'F'
        Assert-NormalFileExact $entry 'D'
        $bytes += [int64]$entry.length
        if (($index % 100) -eq 0 -or $index -eq $Entries.Count) { Write-Host "phase2_d_dry_run_hash_progress=$index/$($Entries.Count)" }
    }
    return $bytes
}

function Invoke-ContractFixture {
    $parsed = '[{"relative_path":"a"},{"relative_path":"b"}]' | ConvertFrom-Json
    $expanded = @(Expand-JsonArrayForPowerShell51 $parsed)
    if ($expanded.Count -ne 2) { throw 'PS5.1 top-level JSON array expansion failed.' }
    Assert-SafeRelativePath 'folder\file.bin'
    $unsafeFailed = $false
    try { Assert-SafeRelativePath '..\escape.bin' } catch { $unsafeFailed = $true }
    if (-not $unsafeFailed) { throw 'Unsafe relative path did not fail closed.' }
    Write-Host 'PHASE2D_RESUMABLE_DELETE_DRY_RUN_PS51_CONTRACT_PASS'
}

try {
    Import-AcceptedPreflightHelpers
    if ($ContractOnly) { Invoke-ContractFixture; return }

    Write-Host '===== PRODUCTION REBALANCE PHASE2 D RESUMABLE DELETE DRY RUN ====='
    Write-Host 'read_only=True'
    Write-Host 'apply_supported=False'
    Write-Host 'data_mutation_performed=False'
    Write-Host 'phase2_d_file_delete_authorized=False'
    Write-Host 'recursive_legacy_raw_root_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'docker_restart_authorized=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host 'clickhouse_mutation_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Phase2D resumable delete dry-run must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Phase2D resumable delete dry-run requires elevated Administrator PowerShell.' }

    $journalResult = Load-AuthorityJournal $AuthorityJournalPath
    $journal = $journalResult.journal
    $manifest = Load-AuthorityManifest $journal
    Write-Host "authority_journal_path=$($journalResult.path)"
    Write-Host "authority_journal_sha256=$($journalResult.sha256)"
    Write-Host "authority_manifest_sha256=$($manifest.sha256)"
    Write-Host "authority_manifest_file_count=$($manifest.entries.Count)"
    Write-Host "authority_manifest_bytes=$($manifest.bytes)"

    if (-not (Normalize-HostPath $LegacyRawRoot).Equals((Normalize-HostPath ([string]$journal.source_root)), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyRawRoot is not frozen authority source.' }
    if (-not (Normalize-HostPath $RawTargetRoot).Equals((Normalize-HostPath ([string]$journal.target_root)), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RawTargetRoot is not frozen authority target.' }
    Assert-NoReparsePoints ([string]$journal.source_root)
    Assert-NoReparsePoints ([string]$journal.target_root)

    Assert-GlobalBoundary $journal 'dry_run_before'
    $verifiedBytes = Invoke-FullShaDryRun $manifest.entries
    if ($verifiedBytes -ne $script:AcceptedManifestBytes) { throw 'Dry-run verified byte total changed.' }
    Assert-GlobalBoundary $journal 'dry_run_final'

    $drive = Get-DriveSnapshot 'D'
    $projected = [int64]$drive.free_bytes + [int64]$manifest.bytes
    $hardResidual = [int64][math]::Max([int64]0, [int64]([int64]$journal.required_hard_free_bytes - $projected))
    $recommendedResidual = [int64][math]::Max([int64]0, [int64]([int64]$journal.required_recommended_free_bytes - $projected))
    if ($hardResidual -ne 0) { throw 'Dry-run no longer clears temporary 20-percent hard floor.' }
    Assert-ExactMain 'exit'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_phase2_d_resumable_delete_dry_run_$timestamp")
    [System.IO.Directory]::CreateDirectory($evidenceDir) | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:ReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_READY_FOR_APPLY'
        next_gate='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_APPLY_IMPLEMENTATION'
        read_only=$true
        data_mutation_performed=$false
        authority_journal_path=$journalResult.path
        authority_journal_sha256=$journalResult.sha256
        authority_manifest_path=$manifest.path
        authority_manifest_sha256=$manifest.sha256
        verified_file_count=[int64]$manifest.entries.Count
        verified_bytes=$verifiedBytes
        hash_mismatch_count=[int64]0
        d_free_before_bytes=[int64]$drive.free_bytes
        d_projected_free_after_authority_bytes=$projected
        d_hard_residual_after_projected_bytes=$hardResidual
        d_recommended_residual_after_projected_bytes=$recommendedResidual
        production_invariant_preserved=$true
        env_unchanged=$true
        protected_visual_processed_unchanged=$true
        phase2_d_file_delete_authorized=$false
        recursive_legacy_raw_root_delete_authorized=$false
        visual_processed_delete_authorized=$false
    }
    $receiptPath = Join-Path $evidenceDir 'production_rebalance_phase2_d_resumable_delete_dry_run.json'
    $receipt | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== PRODUCTION REBALANCE PHASE2 D RESUMABLE DELETE DRY RUN RESULT ====='
    Write-Host 'decision=PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_READY_FOR_APPLY'
    Write-Host 'next_gate=PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_APPLY_IMPLEMENTATION'
    Write-Host 'read_only=True'
    Write-Host 'apply_supported=False'
    Write-Host 'data_mutation_performed=False'
    Write-Host "verified_file_count=$($manifest.entries.Count)"
    Write-Host "verified_bytes=$verifiedBytes"
    Write-Host 'hash_mismatch_count=0'
    Write-Host "authority_manifest_sha256=$($manifest.sha256)"
    Write-Host "authority_journal_sha256=$($journalResult.sha256)"
    Write-Host "dry_run_receipt_path=$receiptPath"
    Write-Host "d_hard_residual_after_projected_bytes=$hardResidual"
    Write-Host "d_recommended_residual_after_projected_bytes=$recommendedResidual"
    Write-Host 'phase2_d_file_delete_authorized=False'
    Write-Host 'recursive_legacy_raw_root_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_DRY_RUN_DONE'
}
finally { Pop-Location }
