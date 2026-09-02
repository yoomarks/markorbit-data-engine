[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyRawRoot = 'D:\yoomarks\markorbit-data-engine\raw_data',
    [string]$RawTargetRoot = 'F:\MarkOrbitData\raw',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
    [string]$EvidenceRoot = 'reports',
    [string]$AcceptedPreflightReceiptPath,
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedPreflightEngineSha = '2f20083a0153e0f7f2568ebd86719adaf3d88b48'
$script:AcceptedManifestFileCount = [int64]1146
$script:AcceptedManifestBytes = [int64]57920246250
$script:JournalVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_JOURNAL_V1'
$script:ReceiptVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_PREPARE_V1'

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
        'Get-AllContainerMounts','Get-ComposeBindMounts','Assert-ComposeRawBindings','Assert-NoReparsePoints',
        'Get-RawDeletionManifest'
    )
    $functions = $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $node.Name
    }, $true)
    foreach ($name in $names) {
        $matches = @($functions | Where-Object { $_.Name -eq $name })
        if ($matches.Count -ne 1) { throw "Expected exactly one accepted helper definition: $name" }
        $functionAst = $matches[0]
        $definitionText = [string]$functionAst.Extent.Text
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
        $imported = Get-Command $name -CommandType Function -ErrorAction Stop
        $expectedParameterNames = @()
        foreach ($parameterAst in $functionAst.Parameters) {
            $expectedParameterNames += [string]$parameterAst.Name.VariablePath.UserPath
        }
        if ($null -ne $functionAst.Body.ParamBlock) {
            foreach ($parameterAst in $functionAst.Body.ParamBlock.Parameters) {
                $expectedParameterNames += [string]$parameterAst.Name.VariablePath.UserPath
            }
        }
        $expectedParameterNames = @($expectedParameterNames | Select-Object -Unique)
        foreach ($parameterName in $expectedParameterNames) {
            if (-not $imported.Parameters.ContainsKey($parameterName)) {
                throw "Imported helper parameter signature was lost: $name.$parameterName"
            }
        }
    }
    Write-Host 'imported_helper_parameter_signatures_preserved=True'
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-TextSha256([string]$Text) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        $hash = $algorithm.ComputeHash($bytes)
        return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally { $algorithm.Dispose() }
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

function Find-AcceptedPreflightReceipt {
    $reportsRoot = Normalize-HostPath (Join-Path $repoRoot 'reports')
    if (-not (Test-Path -LiteralPath $reportsRoot -PathType Container)) { throw 'reports directory is missing.' }
    $candidatePaths = @()
    if (-not [string]::IsNullOrWhiteSpace($AcceptedPreflightReceiptPath)) {
        $candidatePaths = @([System.IO.Path]::GetFullPath($AcceptedPreflightReceiptPath))
    }
    else {
        $directories = @(Get-ChildItem -LiteralPath $reportsRoot -Directory -Filter 'production_rebalance_phase2_d_full_sha256_preflight_*' | Sort-Object LastWriteTime -Descending)
        $candidatePaths = @($directories | ForEach-Object { Join-Path $_.FullName 'production_rebalance_phase2_d_full_sha256_preflight.json' })
    }
    foreach ($path in $candidatePaths) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $full = [System.IO.Path]::GetFullPath($path)
        if (-not (Test-PathContains $reportsRoot $full)) { continue }
        try { $receipt = Get-Content -LiteralPath $full -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { continue }
        if ([string]$receipt.receipt_version -ne 'PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_V1') { continue }
        if ([string]$receipt.engine_sha -ne $script:AcceptedPreflightEngineSha) { continue }
        if ([string]$receipt.decision -ne 'PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_READY') { continue }
        if ([string]$receipt.next_gate -ne 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_DESIGN') { continue }
        if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { continue }
        if ([int64]$receipt.d.verified_file_count -ne $script:AcceptedManifestFileCount) { continue }
        if ([int64]$receipt.d.verified_bytes -ne $script:AcceptedManifestBytes) { continue }
        if ([int64]$receipt.d.hash_mismatch_count -ne 0 -or -not [bool]$receipt.d.source_manifest_stable) { continue }
        if ([int64]$receipt.d.hard_residual_after_projected_bytes -ne 0) { continue }
        if ([int64]$receipt.e.recommended_deficit_bytes -ne 0 -or [bool]$receipt.e.hot_root_exists -or [bool]$receipt.e.logs_root_exists) { continue }
        if (-not [bool]$receipt.production_invariant_preserved -or -not [bool]$receipt.env_unchanged) { continue }
        return [ordered]@{ path=$full; receipt=$receipt }
    }
    throw 'No exact accepted target-host Phase2D full-SHA256 preflight receipt found.'
}

function Assert-PreflightProvenance([object]$Receipt) {
    $preflightSha = ([string]$Receipt.engine_sha).Trim().ToLowerInvariant()
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$preflightSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Accepted Phase2D preflight SHA is not an ancestor of exact main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"${preflightSha}..$ExpectedMainSha")
    $allowed = @(
        'scripts/run-production-rebalance-phase2-d-resumable-apply.ps1',
        'tests/test_production_rebalance_phase2_d_resumable_apply_contract.py',
        '.github/workflows/production-rebalance-phase2-d-resumable-apply-runtime.yml'
    )
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $allowed })
    $missing = @($allowed | Where-Object { $_ -notin $changed })
    Write-Host "preflight_to_current_changed_file_count=$($changed.Count)"
    Write-Host "preflight_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "preflight_to_current_missing_tooling_file_count=$($missing.Count)"
    if ($unexpected.Count -ne 0 -or $missing.Count -ne 0 -or $changed.Count -ne 3) {
        throw 'Accepted preflight provenance is not the exact three-file authority-preparation tooling delta.'
    }
}

function Get-AuthorityManifest([object]$Receipt, [string]$ReceiptPath, [string]$SourceRoot, [string]$TargetRoot, [string]$ProtectedRoot) {
    $manifestPath = [System.IO.Path]::GetFullPath([string]$Receipt.verified_sha256_manifest_path)
    $receiptDirectory = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($ReceiptPath))
    $manifestDirectory = [System.IO.Path]::GetDirectoryName($manifestPath)
    if (-not $manifestDirectory.Equals($receiptDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Authority manifest is not colocated with the accepted preflight receipt.'
    }
    if (-not [System.IO.Path]::GetFileName($manifestPath).Equals('phase2_d_verified_sha256_manifest.json', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Authority manifest filename changed.'
    }
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Authority manifest is missing.' }
    try { $entries = @(Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { throw "Authority manifest is invalid JSON: $($_.Exception.Message)" }
    if ($entries.Count -ne $script:AcceptedManifestFileCount) { throw "Authority manifest file count changed: $($entries.Count)" }
    $seen = @{}
    $bytes = [int64]0
    foreach ($entry in $entries) {
        $relative = [string]$entry.relative_path
        Assert-SafeRelativePath $relative
        $key = $relative.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { throw "Authority manifest contains duplicate path: $relative" }
        $seen[$key] = $true
        $expectedSource = Normalize-HostPath (Join-Path $SourceRoot $relative)
        $expectedTarget = Normalize-HostPath (Join-Path $TargetRoot $relative)
        if (-not (Normalize-HostPath ([string]$entry.source_path)).Equals($expectedSource, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Authority source path mismatch: $relative" }
        if (-not (Normalize-HostPath ([string]$entry.target_path)).Equals($expectedTarget, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Authority target path mismatch: $relative" }
        if (Test-PathContains $ProtectedRoot $expectedSource) { throw "Protected visual_processed leaked into authority manifest: $relative" }
        if (-not [bool]$entry.hash_equal) { throw "Authority manifest contains a hash mismatch: $relative" }
        $sourceSha = ([string]$entry.source_sha256).Trim().ToLowerInvariant()
        $targetSha = ([string]$entry.target_sha256).Trim().ToLowerInvariant()
        if ($sourceSha -notmatch '^[0-9a-f]{64}$' -or $sourceSha -ne $targetSha) { throw "Authority SHA pair is invalid: $relative" }
        $bytes += [int64]$entry.length
    }
    if ($bytes -ne $script:AcceptedManifestBytes) { throw "Authority manifest byte total changed: $bytes" }
    return [ordered]@{
        path=$manifestPath
        sha256=(Get-Sha256 $manifestPath)
        receipt_sha256=(Get-Sha256 $ReceiptPath)
        entries=@($entries)
        bytes=$bytes
    }
}

function Assert-CurrentMetadataMatchesAuthority([object[]]$Entries, [string]$SourceRoot, [string]$TargetRoot, [string]$ProtectedRoot) {
    $current = @(Get-RawDeletionManifest $SourceRoot $ProtectedRoot)
    if ($current.Count -ne $Entries.Count) { throw "Current D candidate count changed: $($current.Count)" }
    $currentMap = @{}
    foreach ($item in $current) { $currentMap[([string]$item.relative_path).ToLowerInvariant()] = $item }
    foreach ($entry in $Entries) {
        $relative = [string]$entry.relative_path
        $key = $relative.ToLowerInvariant()
        if (-not $currentMap.ContainsKey($key)) { throw "Current D candidate is missing: $relative" }
        if ([int64]$currentMap[$key].length -ne [int64]$entry.length) { throw "Current D length changed: $relative" }
        $source = Normalize-HostPath ([string]$entry.source_path)
        $target = Normalize-HostPath ([string]$entry.target_path)
        foreach ($pair in @(@($source,'D'),@($target,'F'))) {
            if (-not (Test-Path -LiteralPath $pair[0] -PathType Leaf)) { throw "$($pair[1]) authority file is missing: $relative" }
            $attributes = [System.IO.File]::GetAttributes($pair[0])
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "$($pair[1]) authority file became a reparse point: $relative" }
            $info = New-Object System.IO.FileInfo($pair[0])
            if ([int64]$info.Length -ne [int64]$entry.length) { throw "$($pair[1]) authority length changed: $relative" }
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
    return Get-TextSha256 ((@($records | Sort-Object) -join "`n"))
}

function Assert-CurrentBindings([string]$SourceRoot, [string]$TargetRoot, [string]$ProtectedRoot, [string]$EnvPath) {
    if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) { throw '.env is required.' }
    $values = Get-DotEnvValues @(Get-Content -LiteralPath $EnvPath -Encoding UTF8)
    $raw = if ($values.ContainsKey('RAW_DATA_PATH')) { Normalize-HostPath ([string]$values['RAW_DATA_PATH']) } else { '' }
    $visualRaw = if ($values.ContainsKey('VISUAL_RAW_PATH')) { Normalize-HostPath ([string]$values['VISUAL_RAW_PATH']) } else { $raw }
    $visualProcessed = if ($values.ContainsKey('VISUAL_PROCESSED_PATH')) { Normalize-HostPath ([string]$values['VISUAL_PROCESSED_PATH']) } else { $ProtectedRoot }
    if (-not $raw.Equals($TargetRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RAW_DATA_PATH is no longer the accepted F Raw target.' }
    if (-not $visualRaw.Equals($TargetRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_RAW_PATH is no longer the accepted F Raw target.' }
    if (-not $visualProcessed.Equals($ProtectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_PROCESSED_PATH is no longer the protected D subtree.' }

    $compose = @(Get-ComposeBindMounts)
    Assert-ComposeRawBindings $compose $ProtectedRoot
    $containers = @(Get-AllContainerMounts)
    $containerRefs = @($containers | Where-Object { $_.normalized_source -and (Test-PathsOverlap $SourceRoot $_.normalized_source) })
    $composeRefs = @($compose | Where-Object { $_.normalized_source -and (Test-PathsOverlap $SourceRoot $_.normalized_source) })
    $unexpectedContainers = @($containerRefs | Where-Object { -not (Test-PathContains $ProtectedRoot $_.normalized_source) })
    $unexpectedCompose = @($composeRefs | Where-Object { -not (Test-PathContains $ProtectedRoot $_.normalized_source) })
    Write-Host "phase2_d_unexpected_container_reference_count=$($unexpectedContainers.Count)"
    Write-Host "phase2_d_unexpected_compose_reference_count=$($unexpectedCompose.Count)"
    if ($unexpectedContainers.Count -ne 0 -or $unexpectedCompose.Count -ne 0) { throw 'D Raw has references outside protected visual_processed.' }
}

function Save-JournalCreateOnly([string]$Path, [object]$Journal) {
    $Journal.updated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $directory = [System.IO.Path]::GetDirectoryName($fullPath)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { [System.IO.Directory]::CreateDirectory($directory) | Out-Null }
    if (Test-Path -LiteralPath $fullPath) { throw 'Authority journal final path already exists; refusing overwrite.' }
    $temporary = Join-Path $directory ('.journal-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporary, ($Journal | ConvertTo-Json -Depth 20), $encoding)
    [System.IO.File]::Move($temporary, $fullPath)
}

function Invoke-ContractFixture {
    if (-not (Get-Command Normalize-HostPath -CommandType Function -ErrorAction SilentlyContinue)) { throw 'Imported helper did not survive in script scope.' }
    $exactMainCommand = Get-Command Assert-ExactMain -CommandType Function -ErrorAction Stop
    if (-not $exactMainCommand.Parameters.ContainsKey('Boundary')) { throw 'Imported Assert-ExactMain lost Boundary parameter.' }
    $normalizeCommand = Get-Command Normalize-HostPath -CommandType Function -ErrorAction Stop
    if (-not $normalizeCommand.Parameters.ContainsKey('Path')) { throw 'Imported Normalize-HostPath lost Path parameter.' }
    if ((Normalize-HostPath 'C:\root\..\target') -ne 'C:\target') { throw 'Imported Normalize-HostPath argument binding failed.' }
    if (-not (Test-PathContains 'C:\root' 'C:\root\child')) { throw 'Imported Test-PathContains argument binding failed.' }
    if (-not (Test-PathsOverlap 'C:\root' 'C:\root\child')) { throw 'Imported Test-PathsOverlap argument binding failed.' }
    $base = Join-Path $env:TEMP ('phase2d-authority-' + [Guid]::NewGuid().ToString('N'))
    [System.IO.Directory]::CreateDirectory($base) | Out-Null
    $journalPath = Join-Path $base 'journal.json'
    $journal = [ordered]@{ journal_version=$script:JournalVersion; state='PREPARED'; mutation_started=$false; completed_relative_paths=@(); inflight_relative_path=$null; updated_at_utc=$null }
    Save-JournalCreateOnly $journalPath $journal
    $loaded = Get-Content -LiteralPath $journalPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$loaded.journal_version -ne $script:JournalVersion -or [bool]$loaded.mutation_started) { throw 'Atomic PREPARED journal fixture failed.' }
    $overwriteFailed = $false
    try { Save-JournalCreateOnly $journalPath $journal } catch { $overwriteFailed = $true }
    if (-not $overwriteFailed) { throw 'Authority journal overwrite did not fail closed.' }
    Assert-SafeRelativePath 'folder\file.bin'
    $unsafeFailed = $false
    try { Assert-SafeRelativePath '..\escape.bin' } catch { $unsafeFailed = $true }
    if (-not $unsafeFailed) { throw 'Unsafe authority relative path did not fail closed.' }
    Write-Host 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_PS51_CONTRACT_PASS'
}

try {
    Import-AcceptedPreflightHelpers

    if ($ContractOnly) {
        Invoke-ContractFixture
        return
    }

    Write-Host '===== PRODUCTION REBALANCE PHASE2 D RESUMABLE AUTHORITY PREPARE ====='
    Write-Host 'read_only=True'
    Write-Host 'apply_supported=False'
    Write-Host 'data_mutation_performed=False'
    Write-Host 'phase2_d_file_delete_authorized=False'
    Write-Host 'recursive_legacy_raw_root_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Phase2D authority preparation must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Phase2D authority preparation requires elevated Administrator PowerShell.' }

    $sourceRoot = Normalize-HostPath $LegacyRawRoot
    $targetRoot = Normalize-HostPath $RawTargetRoot
    $protectedRoot = Normalize-HostPath (Join-Path $sourceRoot 'visual_processed')
    if (-not $sourceRoot.Equals('D:\yoomarks\markorbit-data-engine\raw_data', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyRawRoot changed.' }
    if (-not $targetRoot.Equals('F:\MarkOrbitData\raw', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RawTargetRoot changed.' }
    if (-not (Normalize-HostPath $LegacyEHotRoot).Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotRoot changed.' }
    if (-not (Normalize-HostPath $LegacyEHotLogsRoot).Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotLogsRoot changed.' }
    foreach ($required in @($sourceRoot,$targetRoot,$protectedRoot)) {
        if (-not (Test-Path -LiteralPath $required -PathType Container)) { throw "Required Phase2D directory is missing: $required" }
    }
    if ((Test-Path -LiteralPath $LegacyEHotRoot) -or (Test-Path -LiteralPath $LegacyEHotLogsRoot)) { throw 'Legacy E roots reappeared after accepted Phase1E.' }

    $preflightResult = Find-AcceptedPreflightReceipt
    $preflight = $preflightResult.receipt
    Write-Host "accepted_phase2_preflight_receipt=$($preflightResult.path)"
    Assert-PreflightProvenance $preflight
    if (-not (Normalize-HostPath ([string]$preflight.d.source_root)).Equals($sourceRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Accepted preflight source root changed.' }
    if (-not (Normalize-HostPath ([string]$preflight.d.target_root)).Equals($targetRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Accepted preflight target root changed.' }
    if (-not (Normalize-HostPath ([string]$preflight.d.protected_visual_processed)).Equals($protectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Accepted preflight protected root changed.' }
    if ([string]$preflight.production.accepted_volume -ne $AcceptedVolume) { throw 'Accepted preflight named volume changed.' }

    $authority = Get-AuthorityManifest $preflight $preflightResult.path $sourceRoot $targetRoot $protectedRoot
    Write-Host "authority_manifest_sha256=$($authority.sha256)"
    Write-Host "authority_manifest_file_count=$($authority.entries.Count)"
    Write-Host "authority_manifest_bytes=$($authority.bytes)"

    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_before=$([bool]$productionBefore.ready)"
    if (-not [bool]$productionBefore.ready) { throw 'Production ClickHouse is not healthy.' }
    Assert-AcceptedProductionMount $productionBefore.container_id

    $envPath = Join-Path $repoRoot '.env'
    $envSha = Get-Sha256 $envPath
    Assert-CurrentBindings $sourceRoot $targetRoot $protectedRoot $envPath
    Assert-NoReparsePoints $sourceRoot
    Assert-NoReparsePoints $targetRoot
    Assert-CurrentMetadataMatchesAuthority $authority.entries $sourceRoot $targetRoot $protectedRoot
    $protectedSignature = Get-ProtectedTreeSignature $protectedRoot

    $driveD = Get-DriveSnapshot 'D'
    $requiredHard = [int64]$preflight.d.required_hard_free_bytes
    $requiredRecommended = [int64]$preflight.d.required_recommended_free_bytes
    $projectedFree = [int64]$driveD.free_bytes + [int64]$authority.bytes
    $hardResidual = [int64][math]::Max([int64]0, [int64]($requiredHard - $projectedFree))
    $recommendedResidual = [int64][math]::Max([int64]0, [int64]($requiredRecommended - $projectedFree))
    Write-Host "d_free_before_bytes=$($driveD.free_bytes)"
    Write-Host "d_required_hard_free_bytes=$requiredHard"
    Write-Host "d_required_recommended_free_bytes=$requiredRecommended"
    Write-Host "d_projected_free_after_authority_bytes=$projectedFree"
    Write-Host "d_hard_residual_after_projected_bytes=$hardResidual"
    Write-Host "d_recommended_residual_after_projected_bytes=$recommendedResidual"
    if ($hardResidual -ne 0) { throw 'Frozen authority no longer clears the temporary 20-percent hard floor.' }

    Assert-RawConsumersStopped
    $productionFinal = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_final=$([bool]$productionFinal.ready)"
    if (-not [bool]$productionFinal.ready) { throw 'Production ClickHouse lost health.' }
    Assert-AcceptedProductionMount $productionFinal.container_id
    if (-not (Get-Sha256 $envPath).Equals($envSha, [System.StringComparison]::OrdinalIgnoreCase)) { throw '.env changed during authority preparation.' }
    if (-not (Get-ProtectedTreeSignature $protectedRoot).Equals($protectedSignature, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Protected visual_processed changed during authority preparation.' }
    Assert-ExactMain 'exit'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_phase2_d_resumable_authority_$timestamp")
    [System.IO.Directory]::CreateDirectory($evidenceDir) | Out-Null
    $journalPath = Join-Path $evidenceDir 'phase2_d_resumable_authority_journal.json'
    $journal = [ordered]@{
        journal_version=$script:JournalVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        state='PREPARED'
        phase='awaiting_separate_apply_implementation_and_audit'
        mutation_started=$false
        accepted_preflight_receipt_path=$preflightResult.path
        accepted_preflight_receipt_sha256=$authority.receipt_sha256
        authority_manifest_path=$authority.path
        authority_manifest_sha256=$authority.sha256
        source_root=$sourceRoot
        target_root=$targetRoot
        protected_visual_processed=$protectedRoot
        protected_tree_signature=$protectedSignature
        env_sha256=$envSha
        manifest_file_count=[int64]$authority.entries.Count
        manifest_bytes=[int64]$authority.bytes
        required_hard_free_bytes=$requiredHard
        required_recommended_free_bytes=$requiredRecommended
        projected_free_after_authority_bytes=$projectedFree
        hard_residual_after_projected_bytes=$hardResidual
        recommended_residual_after_projected_bytes=$recommendedResidual
        completed_relative_paths=@()
        inflight_relative_path=$null
        deleted_file_count=[int64]0
        deleted_bytes=[int64]0
        created_at_utc=(Get-Date).ToUniversalTime().ToString('o')
        updated_at_utc=$null
    }
    Save-JournalCreateOnly $journalPath $journal

    $receipt = [ordered]@{
        receipt_version=$script:ReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_PREPARED'
        next_gate='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_IMPLEMENTATION'
        read_only=$true
        data_mutation_performed=$false
        accepted_preflight_receipt_path=$preflightResult.path
        accepted_preflight_receipt_sha256=$authority.receipt_sha256
        authority_manifest_path=$authority.path
        authority_manifest_sha256=$authority.sha256
        journal_path=$journalPath
        manifest_file_count=[int64]$authority.entries.Count
        manifest_bytes=[int64]$authority.bytes
        protected_visual_processed=$protectedRoot
        protected_tree_signature=$protectedSignature
        d_projected_free_after_authority_bytes=$projectedFree
        d_hard_residual_after_projected_bytes=$hardResidual
        d_recommended_residual_after_projected_bytes=$recommendedResidual
        production_invariant_preserved=$true
        env_unchanged=$true
        constraints=[ordered]@{
            phase2_d_file_delete_authorized=$false
            recursive_legacy_raw_root_delete_authorized=$false
            visual_processed_delete_authorized=$false
            accepted_volume_delete_authorized=$false
            docker_restart_authorized=$false
            docker_prune_authorized=$false
            vhdx_create_authorized=$false
            vhdx_delete_authorized=$false
            wsl_shutdown_authorized=$false
            wsl_unmount_authorized=$false
            clickhouse_mutation_authorized=$false
            cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
    }
    $receiptPath = Join-Path $evidenceDir 'production_rebalance_phase2_d_resumable_authority_prepare.json'
    $receipt | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== PRODUCTION REBALANCE PHASE2 D RESUMABLE AUTHORITY RESULT ====='
    Write-Host 'decision=PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_PREPARED'
    Write-Host 'next_gate=PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_IMPLEMENTATION'
    Write-Host 'read_only=True'
    Write-Host 'data_mutation_performed=False'
    Write-Host "authority_manifest_sha256=$($authority.sha256)"
    Write-Host "journal_path=$journalPath"
    Write-Host "d_hard_residual_after_projected_bytes=$hardResidual"
    Write-Host "d_recommended_residual_after_projected_bytes=$recommendedResidual"
    Write-Host 'phase2_d_file_delete_authorized=False'
    Write-Host 'recursive_legacy_raw_root_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_DONE'
}
finally { Pop-Location }
