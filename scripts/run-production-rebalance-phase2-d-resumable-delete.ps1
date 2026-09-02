[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AuthorityJournalPath,
    [string]$ResumeJournalPath,
    [string]$AcceptedFullShaDryRunReceiptPath,
    [string]$AcceptedBoundaryDryRunReceiptPath,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyRawRoot = 'D:\yoomarks\markorbit-data-engine\raw_data',
    [string]$RawTargetRoot = 'F:\MarkOrbitData\raw',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
    [string]$EvidenceRoot = 'reports',
    [switch]$Apply,
    [switch]$AcknowledgeLegacyDRawDuplicateDelete,
    [switch]$AcknowledgeTemporary20PercentFloor,
    [switch]$AcknowledgeResumeAfterPartialFailure,
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedAuthorityEngineSha = '74cc3379fc7ff81f29a9235b7c55a0ffda2f4090'
$script:AcceptedFullShaDryRunEngineSha = '5332086661f4a68bbc93622c511fc16f38f4d89f'
$script:AcceptedAuthorityManifestSha256 = '6cd4399aaaf47aab3c5dde6dfd87dc7a29be676ce0d3da93d3d6e493f2f35253'
$script:AcceptedPreparedJournalSha256 = '9af7822688fad9c9d8bf1facd5088d830591b51875475dcc31dac82d11732324'
$script:AcceptedManifestFileCount = [int64]1146
$script:AcceptedManifestBytes = [int64]57920246250
$script:AuthorityJournalVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_JOURNAL_V1'
$script:FullShaDryRunReceiptVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_DRY_RUN_V1'
$script:BoundaryDryRunReceiptVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_BOUNDARY_DRY_RUN_V1'
$script:ApplyReceiptVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_APPLY_V1'
$script:CurrentInflight = $null

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

function Set-ObjectProperty([object]$Object, [string]$Name, [object]$Value) {
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
    else { $property.Value = $Value }
}

function Save-JournalAtomic([string]$Path, [object]$Journal) {
    Set-ObjectProperty $Journal 'updated_at_utc' ((Get-Date).ToUniversalTime().ToString('o'))
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $directory = [System.IO.Path]::GetDirectoryName($fullPath)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { throw 'Journal directory is missing.' }
    $temporary = Join-Path $directory ('.phase2d-journal-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $backup = Join-Path $directory ('.phase2d-journal-backup-' + [Guid]::NewGuid().ToString('N') + '.json')
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporary, ($Journal | ConvertTo-Json -Depth 40), $encoding)
    try {
        if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
            [System.IO.File]::Replace($temporary, $fullPath, $backup, $true)
            try {
                if (Test-Path -LiteralPath $backup -PathType Leaf) { [System.IO.File]::Delete($backup) }
            }
            catch { Write-Warning "Journal backup cleanup failed: $backup" }
        }
        else {
            [System.IO.File]::Move($temporary, $fullPath)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            try { [System.IO.File]::Delete($temporary) } catch {}
        }
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
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Authority/resume journal path is required.' }
    $full = [System.IO.Path]::GetFullPath($Path)
    $reportsRoot = Normalize-HostPath (Join-Path $repoRoot 'reports')
    if (-not (Test-PathContains $reportsRoot $full)) { throw 'Journal must remain under repository reports.' }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw 'Journal is missing.' }
    try { $journal = Get-Content -LiteralPath $full -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Journal JSON invalid: $($_.Exception.Message)" }
    if ([string]$journal.journal_version -ne $script:AuthorityJournalVersion) { throw 'Journal version changed.' }
    if ([string]$journal.engine_sha -ne $script:AcceptedAuthorityEngineSha) { throw 'Journal authority-freezer engine SHA changed.' }
    if ([string]$journal.authority_manifest_sha256 -ne $script:AcceptedAuthorityManifestSha256) { throw 'Journal authority manifest SHA changed.' }
    if ([int64]$journal.manifest_file_count -ne $script:AcceptedManifestFileCount -or [int64]$journal.manifest_bytes -ne $script:AcceptedManifestBytes) { throw 'Journal manifest dimensions changed.' }
    if ([string]$journal.source_root -ne 'D:\yoomarks\markorbit-data-engine\raw_data') { throw 'Journal source root changed.' }
    if ([string]$journal.target_root -ne 'F:\MarkOrbitData\raw') { throw 'Journal target root changed.' }
    if ([string]$journal.protected_visual_processed -ne 'D:\yoomarks\markorbit-data-engine\raw_data\visual_processed') { throw 'Journal protected root changed.' }
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
        if ($seen.ContainsKey($key)) { throw "Duplicate authority relative path: $relative" }
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

function Assert-NormalFileMetadata([object]$Entry, [string]$Side, [bool]$RequirePresent) {
    $path = if ($Side -eq 'D') { [string]$Entry.source_path } else { [string]$Entry.target_path }
    $exists = Test-Path -LiteralPath $path -PathType Leaf
    if (-not $RequirePresent) {
        if ($exists) { throw "$Side authority file expected absent but exists: $([string]$Entry.relative_path)" }
        return
    }
    if (-not $exists) { throw "$Side authority file missing: $([string]$Entry.relative_path)" }
    $attributes = [System.IO.File]::GetAttributes($path)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or ($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { throw "$Side authority path is not a normal file: $([string]$Entry.relative_path)" }
    $info = New-Object System.IO.FileInfo($path)
    if ([int64]$info.Length -ne [int64]$Entry.length) { throw "$Side authority length changed: $([string]$Entry.relative_path)" }
}

function Assert-NormalFileExact([object]$Entry, [string]$Side, [bool]$RequirePresent) {
    Assert-NormalFileMetadata $Entry $Side $RequirePresent
    if (-not $RequirePresent) { return }
    $path = if ($Side -eq 'D') { [string]$Entry.source_path } else { [string]$Entry.target_path }
    $expectedSha = if ($Side -eq 'D') { ([string]$Entry.source_sha256).Trim().ToLowerInvariant() } else { ([string]$Entry.target_sha256).Trim().ToLowerInvariant() }
    $actualSha = Get-Sha256 $path
    if (-not $actualSha.Equals($expectedSha, [System.StringComparison]::OrdinalIgnoreCase)) { throw "$Side authority SHA changed: $([string]$Entry.relative_path)" }
}

function Assert-AllInitialMetadata([object[]]$Entries) {
    $index = 0
    foreach ($entry in $Entries) {
        $index++
        Assert-NormalFileMetadata $entry 'F' $true
        Assert-NormalFileMetadata $entry 'D' $true
        if (($index % 250) -eq 0 -or $index -eq $Entries.Count) { Write-Host "phase2_d_metadata_progress=$index/$($Entries.Count)" }
    }
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

function Assert-GlobalBoundary([object]$Journal, [string]$Phase) {
    Assert-ExactMain $Phase
    if ((Test-Path -LiteralPath $LegacyEHotRoot) -or (Test-Path -LiteralPath $LegacyEHotLogsRoot)) { throw 'Legacy E roots reappeared.' }
    Assert-RawConsumersStopped
    $production = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_$($Phase)=$([bool]$production.ready)"
    if (-not [bool]$production.ready) { throw "Production ClickHouse unhealthy during $Phase." }
    Assert-AcceptedProductionMount $production.container_id
    Assert-CurrentBindings $Journal
    $protectedSignature = Get-ProtectedTreeSignature ([string]$Journal.protected_visual_processed)
    if (-not $protectedSignature.Equals(([string]$Journal.protected_tree_signature), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Protected visual_processed changed since PREPARED authority.' }
}

function Find-AcceptedFullShaDryRunReceipt([string]$Path, [string]$JournalPath) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Explicit -AcceptedFullShaDryRunReceiptPath is required.' }
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw 'Accepted full-SHA dry-run receipt is missing.' }
    try { $receipt = Get-Content -LiteralPath $full -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Accepted full-SHA dry-run receipt JSON invalid: $($_.Exception.Message)" }
    if ([string]$receipt.receipt_version -ne $script:FullShaDryRunReceiptVersion) { throw 'Full-SHA dry-run receipt version changed.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedFullShaDryRunEngineSha) { throw 'Full-SHA dry-run receipt engine SHA is not accepted.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_READY_FOR_APPLY') { throw 'Full-SHA dry-run receipt is not READY_FOR_APPLY.' }
    if (-not ([System.IO.Path]::GetFullPath([string]$receipt.authority_journal_path)).Equals([System.IO.Path]::GetFullPath($JournalPath), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Full-SHA dry-run journal path changed.' }
    if ([string]$receipt.authority_journal_sha256 -ne $script:AcceptedPreparedJournalSha256) { throw 'Full-SHA dry-run journal SHA is not accepted.' }
    if ([string]$receipt.authority_manifest_sha256 -ne $script:AcceptedAuthorityManifestSha256) { throw 'Full-SHA dry-run manifest SHA changed.' }
    if ([int64]$receipt.verified_file_count -ne $script:AcceptedManifestFileCount -or [int64]$receipt.verified_bytes -ne $script:AcceptedManifestBytes -or [int64]$receipt.hash_mismatch_count -ne 0) { throw 'Full-SHA dry-run dimensions are not accepted.' }
    if ([bool]$receipt.data_mutation_performed) { throw 'Full-SHA dry-run receipt unexpectedly records mutation.' }
    if ([int64]$receipt.d_hard_residual_after_projected_bytes -ne 0) { throw 'Full-SHA dry-run no longer proves hard-floor coverage.' }
    return [ordered]@{ path=$full; sha256=(Get-Sha256 $full); receipt=$receipt }
}

function Find-AcceptedBoundaryDryRunReceipt([string]$Path, [string]$JournalPath, [string]$FullShaReceiptSha) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Actual Apply requires explicit -AcceptedBoundaryDryRunReceiptPath.' }
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw 'Accepted boundary dry-run receipt is missing.' }
    try { $receipt = Get-Content -LiteralPath $full -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Accepted boundary dry-run receipt JSON invalid: $($_.Exception.Message)" }
    if ([string]$receipt.receipt_version -ne $script:BoundaryDryRunReceiptVersion) { throw 'Boundary dry-run receipt version changed.' }
    if ([string]$receipt.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Boundary dry-run receipt engine SHA does not match exact current main.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_BOUNDARY_READY_FOR_APPLY') { throw 'Boundary dry-run receipt is not READY_FOR_APPLY.' }
    if (-not ([System.IO.Path]::GetFullPath([string]$receipt.authority_journal_path)).Equals([System.IO.Path]::GetFullPath($JournalPath), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Boundary dry-run journal path changed.' }
    if ([string]$receipt.authority_journal_sha256 -ne $script:AcceptedPreparedJournalSha256) { throw 'Boundary dry-run journal SHA changed.' }
    if ([string]$receipt.authority_manifest_sha256 -ne $script:AcceptedAuthorityManifestSha256) { throw 'Boundary dry-run manifest SHA changed.' }
    if ([string]$receipt.full_sha_dry_run_receipt_sha256 -ne $FullShaReceiptSha) { throw 'Boundary dry-run full-SHA receipt binding changed.' }
    if ([int64]$receipt.metadata_verified_file_count -ne $script:AcceptedManifestFileCount) { throw 'Boundary dry-run metadata file count changed.' }
    if ([bool]$receipt.data_mutation_performed) { throw 'Boundary dry-run unexpectedly records mutation.' }
    if ([int64]$receipt.d_hard_residual_after_projected_bytes -ne 0) { throw 'Boundary dry-run no longer proves hard-floor coverage.' }
    return [ordered]@{ path=$full; sha256=(Get-Sha256 $full); receipt=$receipt }
}

function Write-BoundaryDryRunReceipt([object]$JournalResult, [object]$Manifest, [object]$FullShaReceipt) {
    $drive = Get-DriveSnapshot 'D'
    $projected = [int64]$drive.free_bytes + [int64]$Manifest.bytes
    $hardResidual = [int64][math]::Max([int64]0, [int64]([int64]$JournalResult.journal.required_hard_free_bytes - $projected))
    $recommendedResidual = [int64][math]::Max([int64]0, [int64]([int64]$JournalResult.journal.required_recommended_free_bytes - $projected))
    if ($hardResidual -ne 0) { throw 'Boundary dry-run no longer reaches temporary 20-percent hard floor.' }
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_phase2_d_resumable_delete_boundary_dry_run_$timestamp")
    [System.IO.Directory]::CreateDirectory($evidenceDir) | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:BoundaryDryRunReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_BOUNDARY_READY_FOR_APPLY'
        next_gate='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_APPLY'
        read_only=$true
        data_mutation_performed=$false
        authority_journal_path=$JournalResult.path
        authority_journal_sha256=$JournalResult.sha256
        authority_manifest_path=$Manifest.path
        authority_manifest_sha256=$Manifest.sha256
        full_sha_dry_run_receipt_path=$FullShaReceipt.path
        full_sha_dry_run_receipt_sha256=$FullShaReceipt.sha256
        metadata_verified_file_count=[int64]$Manifest.entries.Count
        metadata_verified_bytes=[int64]$Manifest.bytes
        d_free_before_bytes=[int64]$drive.free_bytes
        d_projected_free_after_authority_bytes=$projected
        d_hard_residual_after_projected_bytes=$hardResidual
        d_recommended_residual_after_projected_bytes=$recommendedResidual
        production_invariant_preserved=$true
        env_unchanged=$true
        phase2_d_file_delete_authorized=$false
        recursive_legacy_raw_root_delete_authorized=$false
        visual_processed_delete_authorized=$false
    }
    $receiptPath = Join-Path $evidenceDir 'production_rebalance_phase2_d_resumable_delete_boundary_dry_run.json'
    $receipt | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    Write-Host '===== PRODUCTION REBALANCE PHASE2 D RESUMABLE DELETE BOUNDARY DRY RUN RESULT ====='
    Write-Host 'decision=PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_BOUNDARY_READY_FOR_APPLY'
    Write-Host 'next_gate=PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_APPLY'
    Write-Host 'apply_requested=False'
    Write-Host 'data_mutation_performed=False'
    Write-Host "metadata_verified_file_count=$($Manifest.entries.Count)"
    Write-Host "authority_journal_sha256=$($JournalResult.sha256)"
    Write-Host "authority_manifest_sha256=$($Manifest.sha256)"
    Write-Host "full_sha_dry_run_receipt_sha256=$($FullShaReceipt.sha256)"
    Write-Host "boundary_dry_run_receipt_path=$receiptPath"
    Write-Host "d_hard_residual_after_projected_bytes=$hardResidual"
    Write-Host "d_recommended_residual_after_projected_bytes=$recommendedResidual"
    Write-Host 'phase2_d_file_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_BOUNDARY_DRY_RUN_DONE'
}

function Get-CompletedSet([object]$Journal, [object[]]$Entries) {
    $authority = @{}
    foreach ($entry in $Entries) { $authority[([string]$entry.relative_path).ToLowerInvariant()] = $entry }
    $completed = @{}
    $bytes = [int64]0
    foreach ($relative in @(Get-OptionalArrayProperty $Journal 'completed_relative_paths')) {
        $value = [string]$relative
        Assert-SafeRelativePath $value
        $key = $value.ToLowerInvariant()
        if (-not $authority.ContainsKey($key)) { throw "Journal completed path is outside authority: $value" }
        if ($completed.ContainsKey($key)) { throw "Journal completed path duplicated: $value" }
        $completed[$key] = $true
        $bytes += [int64]$authority[$key].length
    }
    if ([int64]$Journal.deleted_file_count -ne $completed.Count -or [int64]$Journal.deleted_bytes -ne $bytes) { throw 'Journal deletion counters do not match completed paths.' }
    return $completed
}

function Assert-ResumeState([object]$Journal, [object[]]$Entries) {
    $completed = Get-CompletedSet $Journal $Entries
    $inflight = [string](Get-OptionalPropertyValue $Journal 'inflight_relative_path')
    foreach ($entry in $Entries) {
        $relative = [string]$entry.relative_path
        $key = $relative.ToLowerInvariant()
        if ($completed.ContainsKey($key)) {
            Assert-NormalFileExact $entry 'F' $true
            Assert-NormalFileMetadata $entry 'D' $false
            continue
        }
        if ($inflight -and $relative.Equals($inflight, [System.StringComparison]::OrdinalIgnoreCase)) {
            Assert-NormalFileExact $entry 'F' $true
            if (Test-Path -LiteralPath ([string]$entry.source_path) -PathType Leaf) { Assert-NormalFileExact $entry 'D' $true }
            continue
        }
        Assert-NormalFileMetadata $entry 'F' $true
        if (-not (Test-Path -LiteralPath ([string]$entry.source_path) -PathType Leaf)) { throw "Pending D authority file absent without journal evidence: $relative" }
        Assert-NormalFileMetadata $entry 'D' $true
    }
    return $completed
}

function Reconcile-InflightForResume([object]$Journal, [object[]]$Entries, [string]$JournalPath) {
    $inflight = [string](Get-OptionalPropertyValue $Journal 'inflight_relative_path')
    if ([string]::IsNullOrWhiteSpace($inflight)) { return 'none' }
    $matches = @($Entries | Where-Object { ([string]$_.relative_path).Equals($inflight, [System.StringComparison]::OrdinalIgnoreCase) })
    if ($matches.Count -ne 1) { throw 'Journal inflight path is not exactly one authority entry.' }
    $item = $matches[0]
    $completed = Get-CompletedSet $Journal $Entries
    if ($completed.ContainsKey($inflight.ToLowerInvariant())) { throw 'Inflight path is already completed.' }
    Assert-NormalFileExact $item 'F' $true
    if (Test-Path -LiteralPath ([string]$item.source_path) -PathType Leaf) {
        Assert-NormalFileExact $item 'D' $true
        Write-Host "resume_inflight_source_present_retry=$inflight"
        return 'retry'
    }
    $completedPaths = @((Get-OptionalArrayProperty $Journal 'completed_relative_paths'))
    $completedPaths += $inflight
    Set-ObjectProperty $Journal 'completed_relative_paths' @($completedPaths)
    Set-ObjectProperty $Journal 'deleted_file_count' ([int64]$Journal.deleted_file_count + 1)
    Set-ObjectProperty $Journal 'deleted_bytes' ([int64]$Journal.deleted_bytes + [int64]$item.length)
    Set-ObjectProperty $Journal 'inflight_relative_path' $null
    Set-ObjectProperty $Journal 'state' 'MUTATING'
    Set-ObjectProperty $Journal 'phase' 'resume_recovered_inflight_completion'
    Save-JournalAtomic $JournalPath $Journal
    Write-Host "resume_inflight_recovered_complete=$inflight"
    return 'recovered'
}

function Delete-AuthorizedSourceFile([object]$Entry) {
    $source = Normalize-HostPath ([string]$Entry.source_path)
    $root = Normalize-HostPath $LegacyRawRoot
    $protected = Normalize-HostPath (Join-Path $root 'visual_processed')
    if (-not (Test-PathContains $root $source)) { throw 'Delete boundary rejected source outside frozen D Raw root.' }
    if (Test-PathContains $protected $source) { throw 'Delete boundary rejected protected visual_processed path.' }
    [System.IO.File]::Delete($source)
    if (Test-Path -LiteralPath $source) { throw "Authorized D source still exists after delete: $([string]$Entry.relative_path)" }
}

function Assert-FrozenResumeReceipts([object]$Journal) {
    if ([string]$Journal.prepared_authority_journal_sha256 -ne $script:AcceptedPreparedJournalSha256) { throw 'Resume journal lost accepted PREPARED journal SHA anchor.' }
    $fullPath = [string](Get-OptionalPropertyValue $Journal 'accepted_full_sha_dry_run_receipt_path')
    $fullSha = [string](Get-OptionalPropertyValue $Journal 'accepted_full_sha_dry_run_receipt_sha256')
    $boundaryPath = [string](Get-OptionalPropertyValue $Journal 'accepted_boundary_dry_run_receipt_path')
    $boundarySha = [string](Get-OptionalPropertyValue $Journal 'accepted_boundary_dry_run_receipt_sha256')
    $applyEngine = [string](Get-OptionalPropertyValue $Journal 'apply_engine_sha')
    if ([string]::IsNullOrWhiteSpace($fullPath) -or [string]::IsNullOrWhiteSpace($boundaryPath) -or [string]::IsNullOrWhiteSpace($applyEngine)) { throw 'Resume journal is missing frozen authorization receipts.' }
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf) -or -not (Get-Sha256 $fullPath).Equals($fullSha, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Frozen full-SHA dry-run receipt changed before resume.' }
    if (-not (Test-Path -LiteralPath $boundaryPath -PathType Leaf) -or -not (Get-Sha256 $boundaryPath).Equals($boundarySha, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Frozen boundary dry-run receipt changed before resume.' }
    try { $boundary = Get-Content -LiteralPath $boundaryPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Frozen boundary dry-run receipt JSON invalid: $($_.Exception.Message)" }
    if ([string]$boundary.receipt_version -ne $script:BoundaryDryRunReceiptVersion -or [string]$boundary.engine_sha -ne $applyEngine) { throw 'Frozen boundary dry-run receipt no longer matches original Apply engine.' }
    if ([string]$boundary.authority_journal_sha256 -ne $script:AcceptedPreparedJournalSha256 -or [string]$boundary.authority_manifest_sha256 -ne $script:AcceptedAuthorityManifestSha256) { throw 'Frozen boundary receipt authority changed.' }
}

function Invoke-ContractFixture {
    $base = Join-Path $env:TEMP ('phase2d-resumable-apply-' + [Guid]::NewGuid().ToString('N'))
    $dRoot = Join-Path $base 'd\raw_data'
    $fRoot = Join-Path $base 'f\raw'
    $protected = Join-Path $dRoot 'visual_processed'
    [System.IO.Directory]::CreateDirectory($protected) | Out-Null
    [System.IO.Directory]::CreateDirectory($fRoot) | Out-Null
    $oldD = $script:LegacyRawRoot
    $oldF = $script:RawTargetRoot
    $script:LegacyRawRoot = $dRoot
    $script:RawTargetRoot = $fRoot
    try {
        $relative1 = 'nested\one.bin'
        $relative2 = 'nested\two.bin'
        $entries = @()
        foreach ($relative in @($relative1,$relative2)) {
            $dFile = Join-Path $dRoot $relative
            $fFile = Join-Path $fRoot $relative
            [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($dFile)) | Out-Null
            [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($fFile)) | Out-Null
            [System.IO.File]::WriteAllText($dFile, "fixture-$relative")
            [System.IO.File]::WriteAllText($fFile, "fixture-$relative")
            $sha = Get-Sha256 $dFile
            $entries += [pscustomobject]@{ relative_path=$relative; source_path=$dFile; target_path=$fFile; length=[int64](New-Object System.IO.FileInfo($dFile)).Length; source_sha256=$sha; target_sha256=$sha; hash_equal=$true }
        }
        $journalPath = Join-Path $base 'journal.json'
        $journal = [pscustomobject]@{
            journal_version=$script:AuthorityJournalVersion; engine_sha=$script:AcceptedAuthorityEngineSha; state='MUTATING'; phase='fixture'; mutation_started=$true;
            authority_manifest_sha256=$script:AcceptedAuthorityManifestSha256; manifest_file_count=[int64]2; manifest_bytes=[int64]($entries[0].length + $entries[1].length);
            source_root=$dRoot; target_root=$fRoot; protected_visual_processed=$protected; completed_relative_paths=@(); inflight_relative_path=$relative1;
            deleted_file_count=[int64]0; deleted_bytes=[int64]0; updated_at_utc=$null
        }
        $encoding = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($journalPath, ($journal | ConvertTo-Json -Depth 20), $encoding)
        Save-JournalAtomic $journalPath $journal
        $reloaded = Get-Content -LiteralPath $journalPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$reloaded.inflight_relative_path -ne $relative1) { throw 'Atomic journal replace fixture failed.' }

        Delete-AuthorizedSourceFile $entries[0]
        $result = Reconcile-InflightForResume $journal $entries $journalPath
        if ($result -ne 'recovered' -or [int64]$journal.deleted_file_count -ne 1 -or -not [string]::IsNullOrWhiteSpace([string]$journal.inflight_relative_path)) { throw 'Crash-after-delete inflight reconciliation fixture failed.' }
        Assert-NormalFileMetadata $entries[0] 'D' $false
        Assert-NormalFileExact $entries[0] 'F' $true

        Set-ObjectProperty $journal 'inflight_relative_path' $relative2
        $result2 = Reconcile-InflightForResume $journal $entries $journalPath
        if ($result2 -ne 'retry' -or [int64]$journal.deleted_file_count -ne 1) { throw 'Crash-before-delete inflight retry fixture failed.' }

        [System.IO.File]::WriteAllText([string]$entries[0].source_path, "fixture-$relative1")
        $completedPresentFailed = $false
        try { Assert-ResumeState $journal $entries | Out-Null } catch { $completedPresentFailed = $true }
        if (-not $completedPresentFailed) { throw 'Completed D-present state did not fail closed.' }
        [System.IO.File]::Delete([string]$entries[0].source_path)

        [System.IO.File]::WriteAllText([string]$entries[1].target_path, 'tampered')
        $tamperFailed = $false
        try { Assert-NormalFileExact $entries[1] 'F' $true } catch { $tamperFailed = $true }
        if (-not $tamperFailed) { throw 'F tamper did not fail closed.' }
        Write-Host 'PHASE2D_RESUMABLE_DELETE_APPLY_PS51_CONTRACT_PASS'
    }
    finally {
        $script:LegacyRawRoot = $oldD
        $script:RawTargetRoot = $oldF
        if (Test-Path -LiteralPath $base) { Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

try {
    Import-AcceptedPreflightHelpers

    if ($ContractOnly) {
        Invoke-ContractFixture
        return
    }

    Write-Host '===== PRODUCTION REBALANCE PHASE2 D RESUMABLE MANIFEST-BOUND DELETE ====='
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "resume_requested=$(-not [string]::IsNullOrWhiteSpace($ResumeJournalPath))"
    Write-Host 'recursive_legacy_raw_root_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'docker_restart_authorized=False'
    Write-Host 'docker_prune_authorized=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host 'wsl_mutation_authorized=False'
    Write-Host 'clickhouse_mutation_authorized=False'
    Write-Host 'replay_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Phase2D resumable delete must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Phase2D resumable delete requires elevated Administrator PowerShell.' }

    $isResume = -not [string]::IsNullOrWhiteSpace($ResumeJournalPath)
    if ($isResume) {
        if (-not [string]::IsNullOrWhiteSpace($AuthorityJournalPath)) { throw 'Resume uses only -ResumeJournalPath, not -AuthorityJournalPath.' }
        if (-not $Apply) { throw 'Resume requires -Apply.' }
        if (-not $AcknowledgeResumeAfterPartialFailure) { throw 'Resume requires -AcknowledgeResumeAfterPartialFailure.' }
        $journalInput = $ResumeJournalPath
    }
    else {
        if ([string]::IsNullOrWhiteSpace($AuthorityJournalPath)) { throw 'Initial boundary dry-run/apply requires -AuthorityJournalPath.' }
        $journalInput = $AuthorityJournalPath
    }
    if ($Apply -and (-not $AcknowledgeLegacyDRawDuplicateDelete -or -not $AcknowledgeTemporary20PercentFloor)) { throw 'Apply requires duplicate-delete and temporary-20%-floor acknowledgements.' }

    $journalResult = Load-AuthorityJournal $journalInput
    $journal = $journalResult.journal
    $manifest = Load-AuthorityManifest $journal
    Write-Host "authority_journal_path=$($journalResult.path)"
    Write-Host "authority_journal_sha256=$($journalResult.sha256)"
    Write-Host "authority_manifest_sha256=$($manifest.sha256)"
    Write-Host "authority_manifest_file_count=$($manifest.entries.Count)"
    Write-Host "authority_manifest_bytes=$($manifest.bytes)"

    if (-not (Normalize-HostPath $LegacyRawRoot).Equals((Normalize-HostPath ([string]$journal.source_root)), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyRawRoot is not frozen authority source root.' }
    if (-not (Normalize-HostPath $RawTargetRoot).Equals((Normalize-HostPath ([string]$journal.target_root)), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RawTargetRoot is not frozen authority target root.' }
    Assert-NoReparsePoints ([string]$journal.source_root)
    Assert-NoReparsePoints ([string]$journal.target_root)

    if (-not $Apply) {
        if ($isResume) { throw 'Resume cannot run in no-Apply mode.' }
        if (-not $journalResult.sha256.Equals($script:AcceptedPreparedJournalSha256, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Boundary dry-run requires exact accepted PREPARED journal SHA.' }
        if ([string]$journal.state -ne 'PREPARED' -or [bool]$journal.mutation_started -or [int64]$journal.deleted_file_count -ne 0 -or [int64]$journal.deleted_bytes -ne 0 -or -not [string]::IsNullOrWhiteSpace([string]$journal.inflight_relative_path)) { throw 'Boundary dry-run requires untouched PREPARED authority journal.' }
        $fullShaReceipt = Find-AcceptedFullShaDryRunReceipt $AcceptedFullShaDryRunReceiptPath $journalResult.path
        Assert-GlobalBoundary $journal 'boundary_dry_run_before'
        Assert-AllInitialMetadata $manifest.entries
        $drive = Get-DriveSnapshot 'D'
        $projected = [int64]$drive.free_bytes + [int64]$manifest.bytes
        $hardResidual = [int64][math]::Max([int64]0, [int64]([int64]$journal.required_hard_free_bytes - $projected))
        if ($hardResidual -ne 0) { throw 'Boundary dry-run no longer reaches temporary 20-percent hard floor.' }
        Assert-GlobalBoundary $journal 'boundary_dry_run_final'
        Assert-ExactMain 'exit'
        Write-BoundaryDryRunReceipt $journalResult $manifest $fullShaReceipt
        return
    }

    $fullShaReceipt = $null
    $boundaryReceipt = $null
    if (-not $isResume) {
        if (-not $journalResult.sha256.Equals($script:AcceptedPreparedJournalSha256, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Initial Apply requires exact accepted PREPARED journal SHA.' }
        if ([string]$journal.state -ne 'PREPARED' -or [bool]$journal.mutation_started -or [int64]$journal.deleted_file_count -ne 0 -or [int64]$journal.deleted_bytes -ne 0) { throw 'Initial Apply requires untouched PREPARED authority journal.' }
        $fullShaReceipt = Find-AcceptedFullShaDryRunReceipt $AcceptedFullShaDryRunReceiptPath $journalResult.path
        $boundaryReceipt = Find-AcceptedBoundaryDryRunReceipt $AcceptedBoundaryDryRunReceiptPath $journalResult.path $fullShaReceipt.sha256
        Assert-AllInitialMetadata $manifest.entries
    }
    else {
        $resumeState = [string]$journal.state
        if (($resumeState -ne 'PARTIAL_FAILURE' -and $resumeState -ne 'MUTATING') -or -not [bool]$journal.mutation_started) { throw 'Resume requires PARTIAL_FAILURE or interrupted MUTATING journal with mutation_started=true.' }
        Assert-FrozenResumeReceipts $journal
        Assert-ResumeState $journal $manifest.entries | Out-Null
    }

    Assert-GlobalBoundary $journal 'destructive_boundary'

    if ($isResume) {
        $resumeSourceState = [string]$journal.state
        $reconcileResult = Reconcile-InflightForResume $journal $manifest.entries $journalResult.path
        Write-Host "resume_inflight_reconcile_result=$reconcileResult"
        Set-ObjectProperty $journal 'last_resume_source_state' $resumeSourceState
    }

    $driveBefore = Get-DriveSnapshot 'D'
    $remainingBytes = [int64]$script:AcceptedManifestBytes - [int64]$journal.deleted_bytes
    if ($remainingBytes -lt 0) { throw 'Journal deleted bytes exceed frozen authority.' }
    $projectedFinal = [int64]$driveBefore.free_bytes + $remainingBytes
    $hardResidual = [int64][math]::Max([int64]0, [int64]([int64]$journal.required_hard_free_bytes - $projectedFinal))
    Write-Host "d_free_before_apply_bytes=$($driveBefore.free_bytes)"
    Write-Host "d_remaining_authority_bytes=$remainingBytes"
    Write-Host "d_projected_final_free_bytes=$projectedFinal"
    Write-Host "d_hard_residual_after_projected_bytes=$hardResidual"
    if ($hardResidual -ne 0) { throw 'Remaining authority reclaim no longer reaches temporary 20-percent hard floor.' }
    Assert-ExactMain 'destructive_boundary_exact_main'

    if ($isResume) {
        Set-ObjectProperty $journal 'state' 'MUTATING'
        Set-ObjectProperty $journal 'phase' 'resume_boundary_accepted'
        Set-ObjectProperty $journal 'last_resume_engine_sha' ($ExpectedMainSha.Trim().ToLowerInvariant())
        Save-JournalAtomic $journalResult.path $journal
    }
    else {
        Set-ObjectProperty $journal 'state' 'MUTATING'
        Set-ObjectProperty $journal 'phase' 'initial_apply_boundary_accepted'
        Set-ObjectProperty $journal 'mutation_started' $true
        Set-ObjectProperty $journal 'prepared_authority_journal_sha256' $script:AcceptedPreparedJournalSha256
        Set-ObjectProperty $journal 'apply_engine_sha' ($ExpectedMainSha.Trim().ToLowerInvariant())
        Set-ObjectProperty $journal 'accepted_full_sha_dry_run_receipt_path' $fullShaReceipt.path
        Set-ObjectProperty $journal 'accepted_full_sha_dry_run_receipt_sha256' $fullShaReceipt.sha256
        Set-ObjectProperty $journal 'accepted_boundary_dry_run_receipt_path' $boundaryReceipt.path
        Set-ObjectProperty $journal 'accepted_boundary_dry_run_receipt_sha256' $boundaryReceipt.sha256
        Save-JournalAtomic $journalResult.path $journal
    }

    try {
        $completed = Get-CompletedSet $journal $manifest.entries
        foreach ($entry in $manifest.entries) {
            $relative = [string]$entry.relative_path
            $key = $relative.ToLowerInvariant()
            if ($completed.ContainsKey($key)) { continue }

            $script:CurrentInflight = $relative
            Set-ObjectProperty $journal 'state' 'MUTATING'
            Set-ObjectProperty $journal 'phase' 'deleting_authorized_file'
            Set-ObjectProperty $journal 'inflight_relative_path' $relative
            Save-JournalAtomic $journalResult.path $journal

            Assert-NormalFileExact $entry 'F' $true
            Assert-NormalFileExact $entry 'D' $true
            Delete-AuthorizedSourceFile $entry

            $completedPaths = @((Get-OptionalArrayProperty $journal 'completed_relative_paths'))
            $completedPaths += $relative
            Set-ObjectProperty $journal 'completed_relative_paths' @($completedPaths)
            Set-ObjectProperty $journal 'deleted_file_count' ([int64]$journal.deleted_file_count + 1)
            Set-ObjectProperty $journal 'deleted_bytes' ([int64]$journal.deleted_bytes + [int64]$entry.length)
            Set-ObjectProperty $journal 'inflight_relative_path' $null
            Save-JournalAtomic $journalResult.path $journal
            $completed[$key] = $true
            $script:CurrentInflight = $null

            if (([int64]$journal.deleted_file_count % 25) -eq 0 -or [int64]$journal.deleted_file_count -eq $script:AcceptedManifestFileCount) {
                Write-Host "phase2_d_delete_progress=$([int64]$journal.deleted_file_count)/$script:AcceptedManifestFileCount bytes=$([int64]$journal.deleted_bytes)"
            }
            if (([int64]$journal.deleted_file_count % 100) -eq 0) {
                Assert-RawConsumersStopped
                $midHealth = Get-ProductionClickHouseHealth
                if (-not [bool]$midHealth.ready) { throw 'Production ClickHouse lost health during deletion.' }
                Assert-AcceptedProductionMount $midHealth.container_id
                Assert-ExactMain 'mid_apply'
            }
        }

        if ([int64]$journal.deleted_file_count -ne $script:AcceptedManifestFileCount -or [int64]$journal.deleted_bytes -ne $script:AcceptedManifestBytes) { throw 'Deletion counters did not reach full frozen authority.' }

        Write-Host 'phase2_d_final_verification=all_D_absent_and_F_sha_exact'
        $verifyIndex = 0
        foreach ($entry in $manifest.entries) {
            $verifyIndex++
            Assert-NormalFileMetadata $entry 'D' $false
            Assert-NormalFileExact $entry 'F' $true
            if (($verifyIndex % 100) -eq 0 -or $verifyIndex -eq $manifest.entries.Count) { Write-Host "phase2_d_final_hash_progress=$verifyIndex/$($manifest.entries.Count)" }
        }

        Assert-GlobalBoundary $journal 'final'
        $driveAfter = Get-DriveSnapshot 'D'
        if ([int64]$driveAfter.free_bytes -lt [int64]$journal.required_hard_free_bytes) { throw 'D free space did not reach temporary 20-percent hard floor.' }
        if (-not (Get-ProtectedTreeSignature ([string]$journal.protected_visual_processed)).Equals(([string]$journal.protected_tree_signature), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Protected visual_processed changed during Apply.' }
        Assert-ExactMain 'exit'

        Set-ObjectProperty $journal 'state' 'GO'
        Set-ObjectProperty $journal 'phase' 'all_authorized_files_deleted_and_verified'
        Set-ObjectProperty $journal 'inflight_relative_path' $null
        Set-ObjectProperty $journal 'final_d_free_bytes' ([int64]$driveAfter.free_bytes)
        Set-ObjectProperty $journal 'completed_at_utc' ((Get-Date).ToUniversalTime().ToString('o'))
        Save-JournalAtomic $journalResult.path $journal

        $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_phase2_d_resumable_delete_apply_$timestamp")
        [System.IO.Directory]::CreateDirectory($evidenceDir) | Out-Null
        $receipt = [ordered]@{
            receipt_version=$script:ApplyReceiptVersion
            engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
            decision='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_GO'
            next_gate='PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH'
            apply_requested=$true
            resume_requested=$isResume
            mutation_performed=$true
            authority_journal_path=$journalResult.path
            authority_manifest_sha256=$manifest.sha256
            deleted_file_count=[int64]$journal.deleted_file_count
            deleted_bytes=[int64]$journal.deleted_bytes
            d_free_before_bytes=[int64]$driveBefore.free_bytes
            d_free_after_bytes=[int64]$driveAfter.free_bytes
            required_hard_free_bytes=[int64]$journal.required_hard_free_bytes
            temporary_hard_floor_met=$true
            preferred_30_percent_floor_claimed=$false
            production_invariant_preserved=$true
            env_unchanged=$true
            protected_visual_processed_unchanged=$true
            recursive_legacy_raw_root_delete_performed=$false
            accepted_volume_mutation_performed=$false
            docker_restart_performed=$false
            docker_prune_performed=$false
            vhdx_mutation_performed=$false
            wsl_mutation_performed=$false
            clickhouse_mutation_performed=$false
            replay_performed=$false
            us_bulk_performed=$false
        }
        $receiptPath = Join-Path $evidenceDir 'production_rebalance_phase2_d_resumable_delete_apply.json'
        $receipt | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
        Write-Host '===== PRODUCTION REBALANCE PHASE2 D RESUMABLE DELETE APPLY RESULT ====='
        Write-Host 'decision=PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_GO'
        Write-Host 'next_gate=PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH'
        Write-Host 'apply_requested=True'
        Write-Host "resume_requested=$isResume"
        Write-Host 'mutation_performed=True'
        Write-Host "deleted_file_count=$([int64]$journal.deleted_file_count)"
        Write-Host "deleted_bytes=$([int64]$journal.deleted_bytes)"
        Write-Host "d_free_after_bytes=$([int64]$driveAfter.free_bytes)"
        Write-Host 'temporary_hard_floor_met=True'
        Write-Host 'preferred_30_percent_floor_claimed=False'
        Write-Host 'production_invariant_preserved=True'
        Write-Host 'env_unchanged=True'
        Write-Host 'protected_visual_processed_unchanged=True'
        Write-Host "journal_path=$($journalResult.path)"
        Write-Host "Evidence directory: $evidenceDir"
        Write-Host 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_DONE'
    }
    catch {
        if ([bool]$journal.mutation_started) {
            try {
                Set-ObjectProperty $journal 'state' 'PARTIAL_FAILURE'
                Set-ObjectProperty $journal 'phase' 'partial_failure_requires_explicit_resume'
                Set-ObjectProperty $journal 'failure_path' $script:CurrentInflight
                Set-ObjectProperty $journal 'failure_message' $_.Exception.Message
                Set-ObjectProperty $journal 'failed_at_utc' ((Get-Date).ToUniversalTime().ToString('o'))
                Save-JournalAtomic $journalResult.path $journal
                Write-Host 'journal_state=PARTIAL_FAILURE'
                Write-Host "journal_failure_path=$script:CurrentInflight"
                Write-Host "journal_path=$($journalResult.path)"
            }
            catch { Write-Warning "Unable to persist PARTIAL_FAILURE journal: $($_.Exception.Message)" }
        }
        throw
    }
}
finally { Pop-Location }
