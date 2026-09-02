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
    [string]$ResumeJournalPath,
    [switch]$AcknowledgeLegacyDRawDuplicateDelete,
    [switch]$AcknowledgeTemporary20PercentFloor,
    [switch]$AcknowledgeResumeAfterPartialFailure,
    [switch]$Apply,
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedPreflightEngineSha = '2f20083a0153e0f7f2568ebd86719adaf3d88b48'
$script:AcceptedManifestFileCount = [int64]1146
$script:AcceptedManifestBytes = [int64]57920246250
$script:JournalVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_JOURNAL_V1'
$script:ReceiptVersion = 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_V1'

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $outputLines = @(& $Command @Arguments 2>&1)
        $nativeExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    $renderedLines = @($outputLines | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $nativeExitCode -ne 0) {
        throw "$Command failed with exit code ${nativeExitCode}: $($renderedLines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$nativeExitCode; lines=@($renderedLines) }
}

function Assert-ExactMain([string]$Boundary) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $headSha = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMainSha = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Boundary"
    Write-Host "HEAD=$headSha"
    Write-Host "origin/main=$originMainSha"
    Write-Host "expected=$expected"
    if ($headSha -ne $expected -or $originMainSha -ne $expected) { throw "Exact main drift detected during $Boundary." }
    if (git status --porcelain) { throw "Working tree must be clean during $Boundary." }
}

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $candidate = $Path.Trim()
    if ($candidate.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) { $candidate = $candidate.Substring(4) }
    if ($candidate.StartsWith('\??\', [System.StringComparison]::OrdinalIgnoreCase)) { $candidate = $candidate.Substring(4) }
    if (-not ([System.IO.Path]::IsPathRooted($candidate) -and $candidate -match '^[A-Za-z]:[\\/]')) { return '' }
    return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
}

function Test-PathContains([string]$ParentPath, [string]$ChildPath) {
    $parent = Normalize-HostPath $ParentPath
    $child = Normalize-HostPath $ChildPath
    if (-not $parent -or -not $child) { return $false }
    if ($child.Equals($parent, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $child.StartsWith($parent + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-PathsOverlap([string]$LeftPath, [string]$RightPath) {
    return [bool]((Test-PathContains $LeftPath $RightPath) -or (Test-PathContains $RightPath $LeftPath))
}

function Get-OptionalPropertyValue([object]$Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-OptionalArrayProperty([object]$Object, [string]$Name) {
    $value = Get-OptionalPropertyValue $Object $Name
    if ($null -eq $value) { return @() }
    return @($value)
}

function Get-DotEnvValues {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines)
    $values = @{}
    foreach ($line in @($Lines)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith('#')) { continue }
        $equals = $trimmed.IndexOf('=')
        if ($equals -le 0) { continue }
        $key = $trimmed.Substring(0, $equals).Trim()
        $value = $trimmed.Substring($equals + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            if ($value.Length -ge 2) { $value = $value.Substring(1, $value.Length - 2) }
        }
        $values[$key] = $value
    }
    return $values
}

function Get-DriveSnapshot([string]$Letter) {
    $root = "${Letter}:\"
    if (-not (Test-Path -LiteralPath $root)) { throw "Required drive missing: $root" }
    $drive = New-Object System.IO.DriveInfo($root)
    return [ordered]@{ drive="${Letter}:"; total_bytes=[int64]$drive.TotalSize; free_bytes=[int64]$drive.AvailableFreeSpace; filesystem=[string]$drive.DriveFormat }
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

function Get-ProductionClickHouseHealth {
    $idProbe = Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse') -AllowFailure
    $ids = @($idProbe.lines | Where-Object { $_.Trim() })
    if ($idProbe.exit_code -ne 0 -or $ids.Count -ne 1) { return [ordered]@{ ready=$false; health=$null; container_id=$null } }
    $containerId = $ids[0].Trim()
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $health = (@($healthProbe.lines) -join '').Trim().ToLowerInvariant()
    $ready = [bool]($healthProbe.exit_code -eq 0 -and $health -eq 'healthy' -and $sqlProbe.exit_code -eq 0 -and ((@($sqlProbe.lines) -join '').Trim() -eq '1'))
    return [ordered]@{ ready=$ready; health=$health; container_id=$containerId }
}

function Assert-AcceptedProductionMount([string]$ContainerId) {
    $probe = Invoke-NativeText 'docker' @('inspect','--format','{{json .Mounts}}',$ContainerId) -AllowFailure
    if ($probe.exit_code -ne 0) { throw 'Unable to inspect production ClickHouse mounts.' }
    try { $mounts = ((@($probe.lines) -join "`n") | ConvertFrom-Json) }
    catch { throw "Production ClickHouse mount JSON invalid: $($_.Exception.Message)" }
    $matches = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    $ready = [bool]($matches.Count -eq 1 -and [string]$matches[0].Type -eq 'volume' -and [string]$matches[0].Name -eq $AcceptedVolume)
    Write-Host "accepted_production_mount_ready=$ready"
    if (-not $ready) { throw 'Production ClickHouse data mount is not the accepted named volume.' }
}

function Assert-RawConsumersStopped {
    $runningTotal = 0
    foreach ($service in @('api','worker','mark-image-worker','qcc-acquisition')) {
        $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','ps','-a','-q',$service) -AllowFailure
        if ($probe.exit_code -ne 0) { throw "Unable to inspect Raw consumer service $service." }
        $running = 0
        foreach ($containerId in @($probe.lines | Where-Object { $_.Trim() })) {
            $state = Invoke-NativeText 'docker' @('inspect','--format','{{.State.Running}}',$containerId.Trim()) -AllowFailure
            if ($state.exit_code -ne 0) { throw "Unable to inspect Raw consumer container for $service." }
            if (((@($state.lines) -join '').Trim().ToLowerInvariant()) -eq 'true') { $running++ }
        }
        $runningTotal += $running
        Write-Host "raw_consumer_service=$service running_count=$running"
    }
    Write-Host "running_raw_consumer_count=$runningTotal"
    if ($runningTotal -ne 0) { throw "All Raw consumer services must remain absent/stopped; observed $runningTotal." }
}

function Get-AllContainerMounts {
    $idsProbe = Invoke-NativeText 'docker' @('ps','-a','-q') -AllowFailure
    if ($idsProbe.exit_code -ne 0) { throw 'Unable to enumerate Docker containers.' }
    $entries = @()
    foreach ($containerId in @($idsProbe.lines | Where-Object { $_.Trim() })) {
        $id = $containerId.Trim()
        $inspectProbe = Invoke-NativeText 'docker' @('inspect','--format','{{json .}}',$id) -AllowFailure
        if ($inspectProbe.exit_code -ne 0) { throw "Unable to inspect container $id." }
        $json = (@($inspectProbe.lines) -join "`n").Trim()
        if (-not $json) { throw "Docker inspect produced no JSON for $id." }
        try { $container = $json | ConvertFrom-Json }
        catch { throw "Docker inspect produced invalid JSON for ${id}: $($_.Exception.Message)" }
        $state = Get-OptionalPropertyValue $container 'State'
        if ($null -eq $state) { throw "Docker inspect omitted State for $id." }
        $runningValue = Get-OptionalPropertyValue $state 'Running'
        if ($null -eq $runningValue) { throw "Docker inspect omitted State.Running for $id." }
        foreach ($mount in @(Get-OptionalArrayProperty $container 'Mounts')) {
            $source = [string](Get-OptionalPropertyValue $mount 'Source')
            $entries += [pscustomobject]@{
                container_id=[string](Get-OptionalPropertyValue $container 'Id')
                container_name=([string](Get-OptionalPropertyValue $container 'Name')).TrimStart('/')
                running=[bool]$runningValue
                source=$source
                normalized_source=(Normalize-HostPath $source)
                destination=[string](Get-OptionalPropertyValue $mount 'Destination')
            }
        }
    }
    return @($entries)
}

function Get-ComposeBindMounts {
    $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','config','--format','json') -AllowFailure
    if ($probe.exit_code -ne 0) { throw 'Unable to resolve current Docker Compose model.' }
    try { $config = ((@($probe.lines) -join "`n") | ConvertFrom-Json) }
    catch { throw "Current Docker Compose model is invalid JSON: $($_.Exception.Message)" }
    $services = Get-OptionalPropertyValue $config 'services'
    if ($null -eq $services) { throw 'Current Docker Compose model omitted services.' }
    $entries = @()
    foreach ($serviceProperty in @($services.PSObject.Properties)) {
        foreach ($mount in @(Get-OptionalArrayProperty $serviceProperty.Value 'volumes')) {
            if ([string](Get-OptionalPropertyValue $mount 'type') -ne 'bind') { continue }
            $source = [string](Get-OptionalPropertyValue $mount 'source')
            $target = [string](Get-OptionalPropertyValue $mount 'target')
            if (-not $source -or -not $target) { throw "Compose bind for service $($serviceProperty.Name) omitted source or target." }
            $entries += [pscustomobject]@{ service=[string]$serviceProperty.Name; source=$source; normalized_source=(Normalize-HostPath $source); target=$target }
        }
    }
    return @($entries)
}

function Assert-ComposeRawBindings([object[]]$ComposeBinds, [string]$ProtectedVisualProcessed) {
    foreach ($service in @('api','worker','mark-image-worker','qcc-acquisition')) {
        $raw = @($ComposeBinds | Where-Object { $_.service -eq $service -and $_.target -eq '/data/raw' })
        if ($raw.Count -ne 1 -or -not (Normalize-HostPath $raw[0].normalized_source).Equals((Normalize-HostPath $RawTargetRoot), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Compose /data/raw for $service does not resolve exactly to accepted F Raw target."
        }
    }
    foreach ($service in @('api','worker','mark-image-worker')) {
        $visualRaw = @($ComposeBinds | Where-Object { $_.service -eq $service -and $_.target -eq '/data/visual-raw' })
        if ($visualRaw.Count -ne 1 -or -not (Normalize-HostPath $visualRaw[0].normalized_source).Equals((Normalize-HostPath $RawTargetRoot), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Compose /data/visual-raw for $service does not resolve exactly to accepted F Raw target."
        }
        $visualProcessed = @($ComposeBinds | Where-Object { $_.service -eq $service -and $_.target -eq '/data/visual-processed' })
        if ($visualProcessed.Count -ne 1 -or -not (Normalize-HostPath $visualProcessed[0].normalized_source).Equals($ProtectedVisualProcessed, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Compose /data/visual-processed for $service does not resolve exactly to protected D subtree."
        }
    }
}

function Assert-ReferenceBoundary([string]$SourceRoot, [string]$ProtectedRoot) {
    $composeBinds = @(Get-ComposeBindMounts)
    Assert-ComposeRawBindings $composeBinds $ProtectedRoot
    $containerMounts = @(Get-AllContainerMounts)
    $dContainerRefs = @($containerMounts | Where-Object { $_.normalized_source -and (Test-PathsOverlap $SourceRoot $_.normalized_source) })
    $dComposeRefs = @($composeBinds | Where-Object { $_.normalized_source -and (Test-PathsOverlap $SourceRoot $_.normalized_source) })
    $unexpectedContainerRefs = @($dContainerRefs | Where-Object { -not (Test-PathContains $ProtectedRoot $_.normalized_source) })
    $unexpectedComposeRefs = @($dComposeRefs | Where-Object { -not (Test-PathContains $ProtectedRoot $_.normalized_source) })
    Write-Host "phase2_d_unexpected_container_reference_count=$($unexpectedContainerRefs.Count)"
    Write-Host "phase2_d_unexpected_compose_reference_count=$($unexpectedComposeRefs.Count)"
    if ($unexpectedContainerRefs.Count -ne 0 -or $unexpectedComposeRefs.Count -ne 0) { throw 'Legacy D Raw has references outside protected visual_processed subtree.' }
}

function Assert-EnvBindings([string]$EnvPath, [string]$ExpectedEnvSha, [string]$TargetRoot, [string]$ProtectedRoot) {
    if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) { throw '.env is required.' }
    $currentSha = Get-Sha256 $EnvPath
    if ($ExpectedEnvSha -and -not $currentSha.Equals($ExpectedEnvSha, [System.StringComparison]::OrdinalIgnoreCase)) { throw '.env changed after Phase2D dry-run authority was frozen.' }
    $values = Get-DotEnvValues @(Get-Content -LiteralPath $EnvPath -Encoding UTF8)
    $raw = if ($values.ContainsKey('RAW_DATA_PATH')) { Normalize-HostPath ([string]$values['RAW_DATA_PATH']) } else { '' }
    $visualRaw = if ($values.ContainsKey('VISUAL_RAW_PATH')) { Normalize-HostPath ([string]$values['VISUAL_RAW_PATH']) } else { $raw }
    $visualProcessed = if ($values.ContainsKey('VISUAL_PROCESSED_PATH')) { Normalize-HostPath ([string]$values['VISUAL_PROCESSED_PATH']) } else { $ProtectedRoot }
    if (-not $raw.Equals($TargetRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RAW_DATA_PATH no longer points to accepted F Raw target.' }
    if (-not $visualRaw.Equals($TargetRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_RAW_PATH no longer points to accepted F Raw target.' }
    if (-not $visualProcessed.Equals($ProtectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_PROCESSED_PATH no longer points to protected D subtree.' }
    return $currentSha
}

function Assert-NoReparsePoints([string]$Root) {
    $normalized = Normalize-HostPath $Root
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized -PathType Container)) { throw "Required directory missing: $Root" }
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($normalized)
    while ($stack.Count -gt 0) {
        $directory = $stack.Pop()
        $attributes = [System.IO.File]::GetAttributes($directory)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point found in Phase2D tree: $directory" }
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
            $entryAttributes = [System.IO.File]::GetAttributes($entry)
            if (($entryAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point found in Phase2D tree: $entry" }
            if (($entryAttributes -band [System.IO.FileAttributes]::Directory) -ne 0) { $stack.Push($entry) }
        }
    }
}

function Get-ProtectedTreeSignature([string]$Root) {
    $normalized = Normalize-HostPath $Root
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized -PathType Container)) { throw 'Protected visual_processed directory is missing.' }
    $records = @()
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($normalized)
    while ($stack.Count -gt 0) {
        $directory = $stack.Pop()
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
            $full = [System.IO.Path]::GetFullPath($entry)
            $attributes = [System.IO.File]::GetAttributes($full)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Protected visual_processed gained a reparse point: $full" }
            $relative = $full.Substring($normalized.Length).TrimStart('\')
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
    $canonical = (@($records | Sort-Object) -join "`n")
    return Get-TextSha256 $canonical
}

function Assert-SafeRelativePath([string]$RelativePath) {
    if ([string]::IsNullOrWhiteSpace($RelativePath)) { throw 'Manifest relative path is empty.' }
    if ([System.IO.Path]::IsPathRooted($RelativePath) -or $RelativePath.Contains(':')) { throw "Manifest relative path is rooted: $RelativePath" }
    foreach ($segment in @($RelativePath -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($segment) -or $segment -eq '.' -or $segment -eq '..') { throw "Manifest relative path contains unsafe segment: $RelativePath" }
    }
}

function Assert-ManifestEntryPaths([object]$Entry, [string]$SourceRoot, [string]$TargetRoot, [string]$ProtectedRoot) {
    $relative = [string]$Entry.relative_path
    Assert-SafeRelativePath $relative
    $expectedSource = Normalize-HostPath (Join-Path $SourceRoot $relative)
    $expectedTarget = Normalize-HostPath (Join-Path $TargetRoot $relative)
    $actualSource = Normalize-HostPath ([string]$Entry.source_path)
    $actualTarget = Normalize-HostPath ([string]$Entry.target_path)
    if (-not $actualSource.Equals($expectedSource, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Manifest source path mismatch: $relative" }
    if (-not $actualTarget.Equals($expectedTarget, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Manifest target path mismatch: $relative" }
    if (-not (Test-PathContains $SourceRoot $actualSource) -or (Test-PathContains $ProtectedRoot $actualSource)) { throw "Manifest source escapes authorized non-protected D boundary: $relative" }
    if (-not (Test-PathContains $TargetRoot $actualTarget)) { throw "Manifest target escapes accepted F boundary: $relative" }
}

function Assert-NormalFileIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int64]$ExpectedLength,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256,
        [Parameter(Mandatory = $true)][string]$Role,
        [switch]$HashContent
    )
    $normalized = Normalize-HostPath $Path
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized -PathType Leaf)) { throw "$Role file is missing: $Path" }
    $attributes = [System.IO.File]::GetAttributes($normalized)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or ($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { throw "$Role is not a normal non-reparse file: $normalized" }
    $info = New-Object System.IO.FileInfo($normalized)
    if ([int64]$info.Length -ne $ExpectedLength) { throw "$Role length mismatch: $normalized" }
    if ($HashContent) {
        $actualSha = Get-Sha256 $normalized
        if (-not $actualSha.Equals($ExpectedSha256.Trim().ToLowerInvariant(), [System.StringComparison]::OrdinalIgnoreCase)) { throw "$Role SHA256 mismatch: $normalized" }
    }
}

function Remove-AuthorizedSourceFile([object]$Entry, [string]$SourceRoot, [string]$ProtectedRoot) {
    Assert-ManifestEntryPaths $Entry $SourceRoot $RawTargetRoot $ProtectedRoot
    $normalized = Normalize-HostPath ([string]$Entry.source_path)
    if ((Test-PathContains $ProtectedRoot $normalized) -or -not (Test-PathContains $SourceRoot $normalized)) { throw 'Delete path is outside the manifest-authorized non-protected D boundary.' }
    $attributes = [System.IO.File]::GetAttributes($normalized)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or ($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { throw "Refusing to delete non-normal source object: $normalized" }
    $info = New-Object System.IO.FileInfo($normalized)
    if ([int64]$info.Length -ne [int64]$Entry.length) { throw "Source length changed immediately before delete: $normalized" }
    [System.IO.File]::Delete($normalized)
    if (Test-Path -LiteralPath $normalized) { throw "Authorized D source file remains after delete: $normalized" }
}

function Get-CurrentCandidateMetadata([string]$SourceRoot, [string]$ProtectedRoot) {
    $source = Normalize-HostPath $SourceRoot
    $protected = Normalize-HostPath $ProtectedRoot
    $prefix = $source + '\'
    $entries = @()
    foreach ($filePath in [System.IO.Directory]::EnumerateFiles($source, '*', [System.IO.SearchOption]::AllDirectories)) {
        $full = [System.IO.Path]::GetFullPath($filePath)
        if (Test-PathContains $protected $full) { continue }
        $attributes = [System.IO.File]::GetAttributes($full)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point found in current D candidate set: $full" }
        $info = New-Object System.IO.FileInfo($full)
        $entries += [pscustomobject]@{ relative_path=$full.Substring($prefix.Length); source_path=$full; length=[int64]$info.Length }
    }
    return @($entries | Sort-Object relative_path)
}

function Find-AcceptedPhase2PreflightReceipt {
    $reportsRoot = Normalize-HostPath (Join-Path $repoRoot 'reports')
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
        if (-not (Test-PathContains $reportsRoot $path)) { continue }
        try { $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { continue }
        if ([string]$receipt.receipt_version -ne 'PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_V1') { continue }
        if ([string]$receipt.engine_sha -ne $script:AcceptedPreflightEngineSha) { continue }
        if ([string]$receipt.decision -ne 'PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_READY') { continue }
        if ([string]$receipt.next_gate -ne 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_DESIGN') { continue }
        if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { continue }
        if ([int64]$receipt.d.verified_file_count -ne $script:AcceptedManifestFileCount -or [int64]$receipt.d.deletable_file_count -ne $script:AcceptedManifestFileCount) { continue }
        if ([int64]$receipt.d.verified_bytes -ne $script:AcceptedManifestBytes -or [int64]$receipt.d.deletable_bytes -ne $script:AcceptedManifestBytes) { continue }
        if ([int64]$receipt.d.hash_mismatch_count -ne 0 -or -not [bool]$receipt.d.source_manifest_stable -or [int64]$receipt.d.hard_residual_after_projected_bytes -ne 0) { continue }
        if ([int64]$receipt.e.recommended_deficit_bytes -ne 0 -or [bool]$receipt.e.hot_root_exists -or [bool]$receipt.e.logs_root_exists) { continue }
        if (-not [bool]$receipt.production_invariant_preserved -or -not [bool]$receipt.env_unchanged -or -not [bool]$receipt.production.accepted_production_mount_ready) { continue }
        return [ordered]@{ path=[System.IO.Path]::GetFullPath($path); receipt=$receipt }
    }
    throw 'No exact accepted Phase2D full-SHA256 preflight receipt found.'
}

function Assert-PreflightReceiptProvenance([object]$Receipt) {
    $preflightSha = ([string]$Receipt.engine_sha).Trim().ToLowerInvariant()
    if ($preflightSha -ne $script:AcceptedPreflightEngineSha) { throw 'Accepted Phase2D preflight SHA is not the frozen target-host authority SHA.' }
    $ancestorProbe = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$preflightSha,$ExpectedMainSha) -AllowFailure
    if ($ancestorProbe.exit_code -ne 0) { throw 'Accepted Phase2D preflight SHA is not an ancestor of current exact main.' }
    $diffProbe = Invoke-NativeText 'git' @('diff','--name-only',"${preflightSha}..$ExpectedMainSha")
    $allowed = @(
        'scripts/run-production-rebalance-phase2-d-resumable-apply.ps1',
        'tests/test_production_rebalance_phase2_d_resumable_apply_contract.py',
        '.github/workflows/production-rebalance-phase2-d-resumable-apply-runtime.yml'
    )
    $changed = @($diffProbe.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $allowed })
    $missing = @($allowed | Where-Object { $_ -notin $changed })
    Write-Host "preflight_to_current_changed_file_count=$($changed.Count)"
    Write-Host "preflight_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "preflight_to_current_missing_tooling_file_count=$($missing.Count)"
    if ($unexpected.Count -ne 0 -or $missing.Count -ne 0 -or $changed.Count -ne $allowed.Count) { throw 'Accepted Phase2D preflight provenance invalidated by changes outside the exact resumable-apply tooling delta.' }
}

function Get-ManifestAuthority([object]$Preflight, [string]$PreflightPath, [string]$SourceRoot, [string]$TargetRoot, [string]$ProtectedRoot) {
    $manifestPath = [System.IO.Path]::GetFullPath([string]$Preflight.verified_sha256_manifest_path)
    $preflightDirectory = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($PreflightPath))
    if (-not [System.IO.Path]::GetDirectoryName($manifestPath).Equals($preflightDirectory, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Verified manifest must remain in the accepted preflight evidence directory.' }
    if (-not [System.IO.Path]::GetFileName($manifestPath).Equals('phase2_d_verified_sha256_manifest.json', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Verified manifest filename changed.' }
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Accepted verified SHA256 manifest is missing.' }
    try { $entries = @(Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { throw "Accepted verified SHA256 manifest is invalid JSON: $($_.Exception.Message)" }
    if ($entries.Count -ne $script:AcceptedManifestFileCount) { throw "Verified manifest file count changed: $($entries.Count)" }
    $seen = @{}
    $bytes = [int64]0
    foreach ($entry in $entries) {
        Assert-ManifestEntryPaths $entry $SourceRoot $TargetRoot $ProtectedRoot
        $relative = [string]$entry.relative_path
        $key = $relative.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { throw "Duplicate relative path in verified manifest: $relative" }
        $seen[$key] = $true
        if (-not [bool]$entry.hash_equal) { throw "Verified manifest contains non-equal hash entry: $relative" }
        $sourceSha = ([string]$entry.source_sha256).Trim().ToLowerInvariant()
        $targetSha = ([string]$entry.target_sha256).Trim().ToLowerInvariant()
        if ($sourceSha -notmatch '^[0-9a-f]{64}$' -or $targetSha -notmatch '^[0-9a-f]{64}$' -or $sourceSha -ne $targetSha) { throw "Verified manifest hash pair invalid: $relative" }
        if ([int64]$entry.length -lt 0) { throw "Verified manifest contains negative length: $relative" }
        $bytes += [int64]$entry.length
    }
    if ($bytes -ne $script:AcceptedManifestBytes) { throw "Verified manifest byte total changed: $bytes" }
    $manifestSha = Get-Sha256 $manifestPath
    $receiptSha = Get-Sha256 $PreflightPath
    return [ordered]@{ path=$manifestPath; sha256=$manifestSha; preflight_receipt_sha256=$receiptSha; entries=@($entries); bytes=$bytes }
}

function Assert-InitialManifestMatchesCurrentSource([object[]]$Entries, [string]$SourceRoot, [string]$ProtectedRoot) {
    $current = @(Get-CurrentCandidateMetadata $SourceRoot $ProtectedRoot)
    if ($current.Count -ne $Entries.Count) { throw "Current D candidate count no longer matches accepted authority: $($current.Count) != $($Entries.Count)" }
    $currentMap = @{}
    foreach ($item in $current) { $currentMap[([string]$item.relative_path).ToLowerInvariant()] = $item }
    foreach ($entry in $Entries) {
        $key = ([string]$entry.relative_path).ToLowerInvariant()
        if (-not $currentMap.ContainsKey($key)) { throw "Current D candidate missing authority entry: $($entry.relative_path)" }
        if ([int64]$currentMap[$key].length -ne [int64]$entry.length) { throw "Current D candidate length changed: $($entry.relative_path)" }
    }
}

function Assert-NoUnauthorizedCurrentSourceFiles([object[]]$Entries, [string]$SourceRoot, [string]$ProtectedRoot) {
    $authority = @{}
    foreach ($entry in $Entries) { $authority[([string]$entry.relative_path).ToLowerInvariant()] = $true }
    foreach ($current in @(Get-CurrentCandidateMetadata $SourceRoot $ProtectedRoot)) {
        $key = ([string]$current.relative_path).ToLowerInvariant()
        if (-not $authority.ContainsKey($key)) { throw "Unapproved file appeared in D candidate root: $($current.relative_path)" }
    }
}

function Save-JournalAtomic([string]$Path, [object]$Journal) {
    $Journal.updated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    $directory = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($Path))
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { [System.IO.Directory]::CreateDirectory($directory) | Out-Null }
    $temporary = Join-Path $directory ('.journal-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $json = $Journal | ConvertTo-Json -Depth 20
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporary, $json, $encoding)
    if (Test-Path -LiteralPath $Path -PathType Leaf) { [System.IO.File]::Replace($temporary, $Path, $null) }
    else { [System.IO.File]::Move($temporary, $Path) }
}

function Load-Journal([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Resume journal missing: $Path" }
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { throw "Resume journal invalid JSON: $($_.Exception.Message)" }
}

function Get-CompletedSet([object]$Journal) {
    $set = @{}
    foreach ($relative in @(Get-OptionalArrayProperty $Journal 'completed_relative_paths')) {
        $value = [string]$relative
        if ([string]::IsNullOrWhiteSpace($value)) { throw 'Journal contains blank completed relative path.' }
        $key = $value.ToLowerInvariant()
        if ($set.ContainsKey($key)) { throw "Journal contains duplicate completed path: $value" }
        $set[$key] = $true
    }
    return $set
}

function Get-ResumeDisposition([hashtable]$CompletedSet, [string]$InflightRelativePath, [string]$RelativePath, [bool]$SourceExists) {
    $key = $RelativePath.ToLowerInvariant()
    if ($CompletedSet.ContainsKey($key)) {
        if ($SourceExists) { throw "Journal says completed but D source still exists: $RelativePath" }
        return 'completed'
    }
    $isInflight = -not [string]::IsNullOrWhiteSpace($InflightRelativePath) -and $InflightRelativePath.Equals($RelativePath, [System.StringComparison]::OrdinalIgnoreCase)
    if ($isInflight) {
        if ($SourceExists) { return 'retry_inflight' }
        return 'recover_completed'
    }
    if (-not $SourceExists) { throw "Pending D source is absent without journal completion/inflight evidence: $RelativePath" }
    return 'pending'
}

function Assert-JournalAuthority([object]$Journal, [string]$JournalPath, [object]$PreflightResult, [object]$Authority, [string]$SourceRoot, [string]$TargetRoot, [string]$ProtectedRoot) {
    if ([string]$Journal.journal_version -ne $script:JournalVersion) { throw 'Unexpected Phase2D resume journal version.' }
    if ([string]$Journal.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Resume journal engine SHA differs from exact main.' }
    if ([string]$Journal.state -eq 'GO') { throw 'Phase2D journal is already GO; replay is forbidden.' }
    if ([string]$Journal.state -notin @('PREPARED','MUTATING','PARTIAL_FAILURE')) { throw "Unsupported resume journal state: $($Journal.state)" }
    if (-not (Normalize-HostPath ([string]$Journal.source_root)).Equals($SourceRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Journal D source root mismatch.' }
    if (-not (Normalize-HostPath ([string]$Journal.target_root)).Equals($TargetRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Journal F target root mismatch.' }
    if (-not (Normalize-HostPath ([string]$Journal.protected_visual_processed)).Equals($ProtectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Journal protected visual_processed mismatch.' }
    if (-not ([System.IO.Path]::GetFullPath([string]$Journal.accepted_preflight_receipt_path)).Equals([System.IO.Path]::GetFullPath($PreflightResult.path), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Journal accepted preflight receipt path mismatch.' }
    if (-not ([string]$Journal.accepted_preflight_receipt_sha256).Equals([string]$Authority.preflight_receipt_sha256, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Accepted preflight receipt bytes changed after dry-run.' }
    if (-not ([System.IO.Path]::GetFullPath([string]$Journal.authority_manifest_path)).Equals([System.IO.Path]::GetFullPath([string]$Authority.path), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Journal authority manifest path mismatch.' }
    if (-not ([string]$Journal.authority_manifest_sha256).Equals([string]$Authority.sha256, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Authority manifest bytes changed after dry-run.' }
    if ([int64]$Journal.manifest_file_count -ne $script:AcceptedManifestFileCount -or [int64]$Journal.manifest_bytes -ne $script:AcceptedManifestBytes) { throw 'Journal manifest totals mismatch.' }
    $reportsRoot = Normalize-HostPath (Join-Path $repoRoot 'reports')
    if (-not (Test-PathContains $reportsRoot ([System.IO.Path]::GetFullPath($JournalPath))) { throw 'Resume journal must remain under repository reports.' }
}

function Get-ReconciledJournalState([object]$Journal, [object[]]$Entries, [string]$SourceRoot, [string]$TargetRoot, [string]$ProtectedRoot, [string]$JournalPath, [switch]$AllowRecoveryWrite) {
    $completed = Get-CompletedSet $Journal
    $inflight = [string](Get-OptionalPropertyValue $Journal 'inflight_relative_path')
    if (-not [string]::IsNullOrWhiteSpace($inflight) -and $completed.ContainsKey($inflight.ToLowerInvariant())) { throw 'Journal inflight path is already completed.' }
    $manifestMap = @{}
    foreach ($entry in $Entries) { $manifestMap[([string]$entry.relative_path).ToLowerInvariant()] = $entry }
    foreach ($completedPath in @($completed.Keys)) { if (-not $manifestMap.ContainsKey($completedPath)) { throw "Journal completion is not in authority manifest: $completedPath" } }
    if (-not [string]::IsNullOrWhiteSpace($inflight) -and -not $manifestMap.ContainsKey($inflight.ToLowerInvariant())) { throw 'Journal inflight path is not in authority manifest.' }

    $recomputedCompletedBytes = [int64]0
    $recomputedCompletedCount = [int64]0
    $remainingBytes = [int64]0
    $recovered = [int64]0
    foreach ($entry in $Entries) {
        $relative = [string]$entry.relative_path
        $sourceExists = Test-Path -LiteralPath ([string]$entry.source_path) -PathType Leaf
        $disposition = Get-ResumeDisposition $completed $inflight $relative $sourceExists
        Assert-NormalFileIdentity -Path ([string]$entry.target_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.target_sha256) -Role 'F authority target'
        switch ($disposition) {
            'completed' {
                Assert-NormalFileIdentity -Path ([string]$entry.target_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.target_sha256) -Role 'F completed target' -HashContent
                $recomputedCompletedCount++
                $recomputedCompletedBytes += [int64]$entry.length
            }
            'recover_completed' {
                Assert-NormalFileIdentity -Path ([string]$entry.target_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.target_sha256) -Role 'F inflight recovery target' -HashContent
                if (-not $AllowRecoveryWrite) { throw "Inflight D source is absent and requires explicit Apply resume to recover journal completion: $relative" }
                $Journal.completed_relative_paths = @($Journal.completed_relative_paths) + @($relative)
                $Journal.inflight_relative_path = $null
                $Journal.deleted_file_count = [int64]$Journal.deleted_file_count + 1
                $Journal.deleted_bytes = [int64]$Journal.deleted_bytes + [int64]$entry.length
                $Journal.recovered_inflight_count = [int64]$Journal.recovered_inflight_count + 1
                Save-JournalAtomic $JournalPath $Journal
                $completed[$relative.ToLowerInvariant()] = $true
                $inflight = ''
                $recomputedCompletedCount++
                $recomputedCompletedBytes += [int64]$entry.length
                $recovered++
            }
            'retry_inflight' {
                Assert-NormalFileIdentity -Path ([string]$entry.source_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.source_sha256) -Role 'D inflight source' -HashContent
                Assert-NormalFileIdentity -Path ([string]$entry.target_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.target_sha256) -Role 'F inflight target' -HashContent
                $remainingBytes += [int64]$entry.length
            }
            'pending' {
                Assert-NormalFileIdentity -Path ([string]$entry.source_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.source_sha256) -Role 'D pending source'
                $remainingBytes += [int64]$entry.length
            }
        }
    }
    if ([int64]$Journal.deleted_file_count -ne $recomputedCompletedCount -or [int64]$Journal.deleted_bytes -ne $recomputedCompletedBytes) { throw 'Journal completed counters disagree with completed path set.' }
    return [ordered]@{ completed_set=$completed; completed_count=$recomputedCompletedCount; completed_bytes=$recomputedCompletedBytes; remaining_bytes=$remainingBytes; recovered_inflight_count=$recovered }
}

function Assert-OperationalBoundary([string]$Boundary, [string]$EnvPath, [string]$EnvSha, [string]$SourceRoot, [string]$TargetRoot, [string]$ProtectedRoot, [string]$ProtectedSignature, [int64]$EMinFreeBytes) {
    Write-Host "phase2_d_boundary=$Boundary"
    Assert-RawConsumersStopped
    $production = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_$Boundary=$([bool]$production.ready)"
    if (-not [bool]$production.ready) { throw "Production ClickHouse lost health at Phase2D boundary $Boundary." }
    Assert-AcceptedProductionMount $production.container_id
    Assert-EnvBindings $EnvPath $EnvSha $TargetRoot $ProtectedRoot | Out-Null
    $currentProtectedSignature = Get-ProtectedTreeSignature $ProtectedRoot
    if (-not $currentProtectedSignature.Equals($ProtectedSignature, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Protected visual_processed metadata changed after dry-run authority was frozen.' }
    if (Test-Path -LiteralPath $LegacyEHotRoot -or Test-Path -LiteralPath $LegacyEHotLogsRoot) { throw 'Legacy E roots reappeared after Phase1E.' }
    $driveE = Get-DriveSnapshot 'E'
    if ([int64]$driveE.free_bytes -lt $EMinFreeBytes) { throw 'E free space regressed below the accepted Phase2D preflight baseline.' }
    Assert-ReferenceBoundary $SourceRoot $ProtectedRoot
}

function Invoke-ContractFixture {
    $fixtureBase = Join-Path $env:TEMP ('phase2d-resume-' + [Guid]::NewGuid().ToString('N'))
    $source = Join-Path $fixtureBase 'source'
    $target = Join-Path $fixtureBase 'target'
    $protected = Join-Path $source 'visual_processed'
    [System.IO.Directory]::CreateDirectory($source) | Out-Null
    [System.IO.Directory]::CreateDirectory($target) | Out-Null
    [System.IO.Directory]::CreateDirectory($protected) | Out-Null
    $sourceFile = Join-Path $source 'one.bin'
    $targetFile = Join-Path $target 'one.bin'
    $protectedFile = Join-Path $protected 'sentinel.bin'
    [System.IO.File]::WriteAllBytes($sourceFile, [byte[]](1,2,3,4))
    [System.IO.File]::WriteAllBytes($targetFile, [byte[]](1,2,3,4))
    [System.IO.File]::WriteAllBytes($protectedFile, [byte[]](9,8,7))
    $sha = Get-Sha256 $sourceFile
    $entry = [pscustomobject]@{ relative_path='one.bin'; source_path=$sourceFile; target_path=$targetFile; length=[int64]4; source_sha256=$sha; target_sha256=$sha; hash_equal=$true }
    Assert-ManifestEntryPaths $entry $source $target $protected
    Assert-NormalFileIdentity -Path $sourceFile -ExpectedLength 4 -ExpectedSha256 $sha -Role 'fixture source' -HashContent
    Assert-NormalFileIdentity -Path $targetFile -ExpectedLength 4 -ExpectedSha256 $sha -Role 'fixture target' -HashContent
    $pending = Get-ResumeDisposition @{} '' 'one.bin' $true
    if ($pending -ne 'pending') { throw 'Pending resume disposition fixture failed.' }
    $journalPath = Join-Path $fixtureBase 'journal.json'
    $journal = [ordered]@{ journal_version=$script:JournalVersion; engine_sha=$ExpectedMainSha; state='MUTATING'; inflight_relative_path='one.bin'; completed_relative_paths=@(); deleted_file_count=[int64]0; deleted_bytes=[int64]0; recovered_inflight_count=[int64]0; updated_at_utc=$null }
    Save-JournalAtomic $journalPath $journal
    $journal.phase='fixture_second_atomic_save'
    Save-JournalAtomic $journalPath $journal
    $loaded = Load-Journal $journalPath
    if ([string]$loaded.inflight_relative_path -ne 'one.bin') { throw 'Atomic journal fixture failed.' }
    Remove-AuthorizedSourceFile $entry $source $protected
    if (-not (Test-Path -LiteralPath $targetFile -PathType Leaf) -or -not (Test-Path -LiteralPath $protectedFile -PathType Leaf)) { throw 'Authorized delete escaped source file boundary in fixture.' }
    $recover = Get-ResumeDisposition @{} 'one.bin' 'one.bin' $false
    if ($recover -ne 'recover_completed') { throw 'Inflight absent recovery disposition fixture failed.' }
    $pendingAbsentFailed = $false
    try { Get-ResumeDisposition @{} '' 'one.bin' $false | Out-Null }
    catch { $pendingAbsentFailed = $true }
    if (-not $pendingAbsentFailed) { throw 'Pending absent source did not fail closed.' }
    [System.IO.File]::WriteAllBytes($sourceFile, [byte[]](1,2,3,4))
    $completedPresentFailed = $false
    try { Get-ResumeDisposition @{ 'one.bin'=$true } '' 'one.bin' $true | Out-Null }
    catch { $completedPresentFailed = $true }
    if (-not $completedPresentFailed) { throw 'Completed source reappearance did not fail closed.' }
    [System.IO.File]::WriteAllBytes($targetFile, [byte[]](5,6,7,8))
    $tamperFailed = $false
    try { Assert-NormalFileIdentity -Path $targetFile -ExpectedLength 4 -ExpectedSha256 $sha -Role 'tampered target' -HashContent }
    catch { $tamperFailed = $true }
    if (-not $tamperFailed) { throw 'F target tamper did not fail closed.' }
    Write-Host 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_PS51_CONTRACT_PASS'
}

try {
    if ($ContractOnly) {
        Invoke-ContractFixture
        return
    }

    Write-Host '===== PRODUCTION REBALANCE PHASE2 D RESUMABLE APPLY ====='
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "legacy_d_duplicate_delete_acknowledged=$([bool]$AcknowledgeLegacyDRawDuplicateDelete)"
    Write-Host "temporary_20_percent_floor_acknowledged=$([bool]$AcknowledgeTemporary20PercentFloor)"
    Write-Host "resume_after_partial_failure_acknowledged=$([bool]$AcknowledgeResumeAfterPartialFailure)"
    Write-Host 'recursive_legacy_raw_root_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Phase2D resumable apply must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Phase2D resumable apply requires elevated Administrator PowerShell.' }
    if ($Apply -and [string]::IsNullOrWhiteSpace($ResumeJournalPath)) { throw '-Apply is forbidden without -ResumeJournalPath from an audited no-Apply dry-run.' }
    if ($Apply -and -not $AcknowledgeLegacyDRawDuplicateDelete) { throw '-Apply requires explicit -AcknowledgeLegacyDRawDuplicateDelete.' }
    if ($Apply -and -not $AcknowledgeTemporary20PercentFloor) { throw '-Apply requires explicit -AcknowledgeTemporary20PercentFloor.' }

    $sourceRoot = Normalize-HostPath $LegacyRawRoot
    $targetRoot = Normalize-HostPath $RawTargetRoot
    $eHot = Normalize-HostPath $LegacyEHotRoot
    $eLogs = Normalize-HostPath $LegacyEHotLogsRoot
    $protectedRoot = Normalize-HostPath (Join-Path $sourceRoot 'visual_processed')
    if (-not $sourceRoot.Equals('D:\yoomarks\markorbit-data-engine\raw_data', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyRawRoot must remain exact approved D Raw root.' }
    if (-not $targetRoot.Equals('F:\MarkOrbitData\raw', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RawTargetRoot must remain exact accepted F Raw root.' }
    if (-not $eHot.Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase) -or -not $eLogs.Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Legacy E boundary changed.' }
    if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container) -or -not (Test-Path -LiteralPath $targetRoot -PathType Container) -or -not (Test-Path -LiteralPath $protectedRoot -PathType Container)) { throw 'Required D/F/protected directory boundary is missing.' }

    $envPath = Join-Path $repoRoot '.env'
    $preflightResult = Find-AcceptedPhase2PreflightReceipt
    $preflight = $preflightResult.receipt
    Write-Host "accepted_phase2_preflight_receipt=$($preflightResult.path)"
    Assert-PreflightReceiptProvenance $preflight
    if (-not (Normalize-HostPath ([string]$preflight.d.source_root)).Equals($sourceRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Accepted preflight D source root changed.' }
    if (-not (Normalize-HostPath ([string]$preflight.d.target_root)).Equals($targetRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Accepted preflight F target root changed.' }
    if (-not (Normalize-HostPath ([string]$preflight.d.protected_visual_processed)).Equals($protectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Accepted preflight protected visual_processed changed.' }
    if ([string]$preflight.production.accepted_volume -ne $AcceptedVolume) { throw 'Accepted preflight named volume changed.' }

    $authority = Get-ManifestAuthority $preflight $preflightResult.path $sourceRoot $targetRoot $protectedRoot
    Write-Host "authority_manifest_path=$($authority.path)"
    Write-Host "authority_manifest_sha256=$($authority.sha256)"
    Write-Host "authority_manifest_file_count=$($authority.entries.Count)"
    Write-Host "authority_manifest_bytes=$($authority.bytes)"

    $candidateReceiptPath = [System.IO.Path]::GetFullPath([string]$preflight.fresh_candidate_receipt_path)
    if (-not (Test-Path -LiteralPath $candidateReceiptPath -PathType Leaf)) { throw 'Accepted preflight fresh candidate receipt is missing.' }
    try { $candidateReceipt = Get-Content -LiteralPath $candidateReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw 'Accepted preflight fresh candidate receipt is invalid JSON.' }
    if ([string]$candidateReceipt.receipt_version -ne 'PRODUCTION_STORAGE_REBALANCE_CANDIDATE_INVENTORY_V1') { throw 'Accepted preflight candidate receipt version changed.' }
    $eMinFreeBytes = [int64]$candidateReceipt.drives.E.free_bytes
    $requiredHardFreeBytes = [int64]$preflight.d.required_hard_free_bytes
    $requiredRecommendedFreeBytes = [int64]$preflight.d.required_recommended_free_bytes

    Assert-NoReparsePoints $sourceRoot
    Assert-NoReparsePoints $targetRoot
    Assert-NoUnauthorizedCurrentSourceFiles $authority.entries $sourceRoot $protectedRoot

    if ([string]::IsNullOrWhiteSpace($ResumeJournalPath)) {
        if ($Apply) { throw 'Fresh direct Apply is forbidden.' }
        Assert-InitialManifestMatchesCurrentSource $authority.entries $sourceRoot $protectedRoot
        $envSha = Assert-EnvBindings $envPath '' $targetRoot $protectedRoot
        $protectedSignature = Get-ProtectedTreeSignature $protectedRoot
        Assert-OperationalBoundary 'dry_run' $envPath $envSha $sourceRoot $targetRoot $protectedRoot $protectedSignature $eMinFreeBytes
        $driveD = Get-DriveSnapshot 'D'
        $projectedFree = [int64]$driveD.free_bytes + [int64]$authority.bytes
        $hardResidual = [int64][math]::Max([int64]0, [int64]($requiredHardFreeBytes - $projectedFree))
        $recommendedResidual = [int64][math]::Max([int64]0, [int64]($requiredRecommendedFreeBytes - $projectedFree))
        Write-Host "d_free_before_bytes=$($driveD.free_bytes)"
        Write-Host "d_required_hard_free_bytes=$requiredHardFreeBytes"
        Write-Host "d_required_recommended_free_bytes=$requiredRecommendedFreeBytes"
        Write-Host "d_projected_free_after_authorized_reclaim_bytes=$projectedFree"
        Write-Host "d_hard_residual_after_projected_bytes=$hardResidual"
        Write-Host "d_recommended_residual_after_projected_bytes=$recommendedResidual"
        if ($hardResidual -ne 0) { throw 'Authorized D reclaim no longer clears temporary 20-percent hard floor.' }
        Assert-ExactMain 'dry_run_boundary'

        $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_phase2_d_resumable_apply_$timestamp")
        [System.IO.Directory]::CreateDirectory($evidenceDir) | Out-Null
        $journalPath = Join-Path $evidenceDir 'phase2_d_resumable_apply_journal.json'
        $journal = [ordered]@{
            journal_version=$script:JournalVersion
            engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
            state='PREPARED'
            phase='ready_for_apply'
            mutation_started=$false
            apply_requested=$false
            delete_acknowledged=$false
            temporary_20_percent_floor_acknowledged=$false
            accepted_preflight_receipt_path=$preflightResult.path
            accepted_preflight_receipt_sha256=$authority.preflight_receipt_sha256
            accepted_preflight_engine_sha=[string]$preflight.engine_sha
            authority_manifest_path=$authority.path
            authority_manifest_sha256=$authority.sha256
            source_root=$sourceRoot
            target_root=$targetRoot
            protected_visual_processed=$protectedRoot
            protected_tree_signature=$protectedSignature
            manifest_file_count=[int64]$authority.entries.Count
            manifest_bytes=[int64]$authority.bytes
            required_hard_free_bytes=$requiredHardFreeBytes
            required_recommended_free_bytes=$requiredRecommendedFreeBytes
            accepted_e_min_free_bytes=$eMinFreeBytes
            env_sha256_before=$envSha
            completed_relative_paths=@()
            inflight_relative_path=$null
            deleted_file_count=[int64]0
            deleted_bytes=[int64]0
            recovered_inflight_count=[int64]0
            failure=$null
            created_at_utc=(Get-Date).ToUniversalTime().ToString('o')
            updated_at_utc=$null
        }
        Save-JournalAtomic $journalPath $journal
        $decision='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_READY_FOR_APPLY'
        $nextGate='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY'
        $applyAccepted=$false
        $mutationPerformed=$false
        $receiptPath = Join-Path $evidenceDir 'production_rebalance_phase2_d_resumable_apply_dry_run.json'
    }
    else {
        $journalPath = [System.IO.Path]::GetFullPath($ResumeJournalPath)
        $journal = Load-Journal $journalPath
        Assert-JournalAuthority $journal $journalPath $preflightResult $authority $sourceRoot $targetRoot $protectedRoot
        $journalStateAtEntry = [string]$journal.state
        if ($Apply -and [bool]$journal.mutation_started -and $journalStateAtEntry -in @('MUTATING','PARTIAL_FAILURE') -and -not $AcknowledgeResumeAfterPartialFailure) {
            throw 'A journal with started/partial mutation requires explicit -AcknowledgeResumeAfterPartialFailure after operator audit.'
        }
        if (-not $Apply) {
            Write-Host "resume_journal_state=$journalStateAtEntry"
            Write-Host 'mutation_performed=False'
            Write-Host "Journal: $journalPath"
            return
        }
        Assert-OperationalBoundary 'resume_pre_mutation' $envPath ([string]$journal.env_sha256_before) $sourceRoot $targetRoot $protectedRoot ([string]$journal.protected_tree_signature) ([int64]$journal.accepted_e_min_free_bytes)
        Assert-NoUnauthorizedCurrentSourceFiles $authority.entries $sourceRoot $protectedRoot
        $reconciled = Get-ReconciledJournalState $journal $authority.entries $sourceRoot $targetRoot $protectedRoot $journalPath -AllowRecoveryWrite
        $driveDBeforeMutation = Get-DriveSnapshot 'D'
        $projectedFromRemaining = [int64]$driveDBeforeMutation.free_bytes + [int64]$reconciled.remaining_bytes
        if ($projectedFromRemaining -lt [int64]$journal.required_hard_free_bytes) { throw 'Remaining authorized reclaim no longer reaches the temporary 20-percent hard floor.' }
        Assert-ExactMain 'destructive_boundary'

        $journal.state='MUTATING'
        $journal.phase='delete_authorized_manifest_files'
        $journal.mutation_started=$true
        $journal.apply_requested=$true
        $journal.delete_acknowledged=[bool]$AcknowledgeLegacyDRawDuplicateDelete
        $journal.temporary_20_percent_floor_acknowledged=[bool]$AcknowledgeTemporary20PercentFloor
        $journal.failure=$null
        Save-JournalAtomic $journalPath $journal
        Write-Host "Journal: $journalPath"
        Write-Host 'phase2_d_apply_stage=manifest_bound_file_delete'

        try {
            $completed = Get-CompletedSet $journal
            $processedThisRun = [int64]0
            foreach ($entry in $authority.entries) {
                $relative = [string]$entry.relative_path
                $key = $relative.ToLowerInvariant()
                if ($completed.ContainsKey($key)) { continue }
                $sourceExists = Test-Path -LiteralPath ([string]$entry.source_path) -PathType Leaf
                $inflight = [string](Get-OptionalPropertyValue $journal 'inflight_relative_path')
                $disposition = Get-ResumeDisposition $completed $inflight $relative $sourceExists
                if ($disposition -eq 'recover_completed') {
                    Assert-NormalFileIdentity -Path ([string]$entry.target_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.target_sha256) -Role 'F recovered target' -HashContent
                    $journal.completed_relative_paths = @($journal.completed_relative_paths) + @($relative)
                    $journal.inflight_relative_path = $null
                    $journal.deleted_file_count = [int64]$journal.deleted_file_count + 1
                    $journal.deleted_bytes = [int64]$journal.deleted_bytes + [int64]$entry.length
                    $journal.recovered_inflight_count = [int64]$journal.recovered_inflight_count + 1
                    Save-JournalAtomic $journalPath $journal
                    $completed[$key] = $true
                    continue
                }
                if ($disposition -notin @('pending','retry_inflight')) { continue }

                $journal.inflight_relative_path=$relative
                $journal.phase='verify_then_delete'
                Save-JournalAtomic $journalPath $journal
                Assert-NormalFileIdentity -Path ([string]$entry.source_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.source_sha256) -Role 'D source before delete' -HashContent
                Assert-NormalFileIdentity -Path ([string]$entry.target_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.target_sha256) -Role 'F target before delete' -HashContent
                Remove-AuthorizedSourceFile $entry $sourceRoot $protectedRoot
                $journal.completed_relative_paths = @($journal.completed_relative_paths) + @($relative)
                $journal.inflight_relative_path = $null
                $journal.deleted_file_count = [int64]$journal.deleted_file_count + 1
                $journal.deleted_bytes = [int64]$journal.deleted_bytes + [int64]$entry.length
                $journal.phase='delete_authorized_manifest_files'
                Save-JournalAtomic $journalPath $journal
                $completed[$key] = $true
                $processedThisRun++
                $completedCount = [int64]$journal.deleted_file_count
                if (($completedCount % 25) -eq 0 -or $completedCount -eq $script:AcceptedManifestFileCount) { Write-Host "phase2_d_apply_progress=$completedCount/$script:AcceptedManifestFileCount" }
                if (($completedCount % 100) -eq 0 -and $completedCount -lt $script:AcceptedManifestFileCount) {
                    Assert-OperationalBoundary "progress_$completedCount" $envPath ([string]$journal.env_sha256_before) $sourceRoot $targetRoot $protectedRoot ([string]$journal.protected_tree_signature) ([int64]$journal.accepted_e_min_free_bytes)
                    Assert-ExactMain "delete_progress_$completedCount"
                }
            }
        }
        catch {
            $journal.state='PARTIAL_FAILURE'
            $journal.phase='partial_failure'
            $journal.failure=[ordered]@{ message=$_.Exception.Message; inflight_relative_path=[string](Get-OptionalPropertyValue $journal 'inflight_relative_path'); failed_at_utc=(Get-Date).ToUniversalTime().ToString('o') }
            try { Save-JournalAtomic $journalPath $journal } catch { Write-Host "journal_partial_failure_persist_error=$($_.Exception.Message)" }
            throw "Phase2D resumable apply entered PARTIAL_FAILURE. Do not manually delete or blindly rerun. Journal: ${journalPath}. Error: $($journal.failure.message)"
        }

        $completedFinal = Get-CompletedSet $journal
        if ($completedFinal.Count -ne $script:AcceptedManifestFileCount -or [int64]$journal.deleted_file_count -ne $script:AcceptedManifestFileCount -or [int64]$journal.deleted_bytes -ne $script:AcceptedManifestBytes) { throw 'Phase2D journal did not complete the full immutable authority manifest.' }
        if (-not [string]::IsNullOrWhiteSpace([string](Get-OptionalPropertyValue $journal 'inflight_relative_path'))) { throw 'Phase2D journal still has an inflight path after deletion loop.' }
        Assert-NoUnauthorizedCurrentSourceFiles $authority.entries $sourceRoot $protectedRoot
        $remainingCurrent = @(Get-CurrentCandidateMetadata $sourceRoot $protectedRoot)
        if ($remainingCurrent.Count -ne 0) { throw "Authorized D duplicate files remain after apply: $($remainingCurrent.Count)" }
        foreach ($entry in $authority.entries) {
            if (Test-Path -LiteralPath ([string]$entry.source_path) { throw "Authorized D source still exists after completion: $($entry.relative_path)" }
            Assert-NormalFileIdentity -Path ([string]$entry.target_path) -ExpectedLength ([int64]$entry.length) -ExpectedSha256 ([string]$entry.target_sha256) -Role 'F final target metadata'
        }
        Assert-OperationalBoundary 'post_delete' $envPath ([string]$journal.env_sha256_before) $sourceRoot $targetRoot $protectedRoot ([string]$journal.protected_tree_signature) ([int64]$journal.accepted_e_min_free_bytes)
        $driveDAfter = Get-DriveSnapshot 'D'
        if ([int64]$driveDAfter.free_bytes -lt [int64]$journal.required_hard_free_bytes) { throw 'D free space is below the accepted temporary 20-percent hard floor after Phase2D.' }
        $recommendedResidual = [int64][math]::Max([int64]0, [int64]([int64]$journal.required_recommended_free_bytes - [int64]$driveDAfter.free_bytes))
        Write-Host "d_free_after_bytes=$($driveDAfter.free_bytes)"
        Write-Host "d_required_hard_free_bytes=$($journal.required_hard_free_bytes)"
        Write-Host "d_required_recommended_free_bytes=$($journal.required_recommended_free_bytes)"
        Write-Host "d_recommended_residual_after_apply_bytes=$recommendedResidual"
        Assert-ExactMain 'exit'

        $journal.state='GO'
        $journal.phase='complete'
        $journal.failure=$null
        Save-JournalAtomic $journalPath $journal
        $decision='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_GO'
        $nextGate='PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH'
        $applyAccepted=$true
        $mutationPerformed=$true
        $receiptPath = Join-Path ([System.IO.Path]::GetDirectoryName($journalPath)) 'production_rebalance_phase2_d_resumable_apply.json'
        $projectedFree=[int64]$driveDAfter.free_bytes
        $hardResidual=[int64]0
    }

    $productionFinal = Get-ProductionClickHouseHealth
    if (-not [bool]$productionFinal.ready) { throw 'Production ClickHouse must remain healthy at Phase2D final receipt boundary.' }
    Assert-AcceptedProductionMount $productionFinal.container_id
    Assert-RawConsumersStopped
    $envUnchanged = [bool]((Get-Sha256 $envPath).Equals([string]$journal.env_sha256_before, [System.StringComparison]::OrdinalIgnoreCase))
    if (-not $envUnchanged) { throw '.env changed during Phase2D resumable apply.' }
    $protectedUnchanged = [bool]((Get-ProtectedTreeSignature $protectedRoot).Equals([string]$journal.protected_tree_signature, [System.StringComparison]::OrdinalIgnoreCase))
    if (-not $protectedUnchanged) { throw 'Protected visual_processed changed during Phase2D resumable apply.' }

    $receipt = [ordered]@{
        receipt_version=$script:ReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision=$decision
        next_gate=$nextGate
        apply_requested=[bool]$Apply
        apply_accepted=$applyAccepted
        mutation_performed=$mutationPerformed
        accepted_preflight_receipt_path=$preflightResult.path
        accepted_preflight_receipt_sha256=$authority.preflight_receipt_sha256
        authority_manifest_path=$authority.path
        authority_manifest_sha256=$authority.sha256
        journal_path=$journalPath
        manifest=[ordered]@{ file_count=[int64]$authority.entries.Count; bytes=[int64]$authority.bytes; original_authority_preserved=$true; target_sha256_verified_at_each_delete_boundary=[bool]$Apply }
        capacity=[ordered]@{ required_hard_free_bytes=$requiredHardFreeBytes; required_recommended_free_bytes=$requiredRecommendedFreeBytes; projected_or_final_free_bytes=[int64]$projectedFree; hard_residual_bytes=[int64]$hardResidual; preferred_30_percent_exception_remains=[bool]($requiredRecommendedFreeBytes -gt $projectedFree) }
        protected=[ordered]@{ path=$protectedRoot; tree_signature=[string]$journal.protected_tree_signature; unchanged=$protectedUnchanged; delete_authorized=$false }
        production=[ordered]@{ accepted_volume=$AcceptedVolume; clickhouse_ready_final=[bool]$productionFinal.ready; accepted_production_mount_ready=$true; running_raw_consumer_count=0 }
        constraints=[ordered]@{
            recursive_legacy_raw_root_delete_authorized=$false
            visual_processed_delete_authorized=$false
            further_phase2_d_file_delete_authorized=$false
            env_write_authorized=$false
            accepted_volume_delete_authorized=$false
            accepted_volume_move_authorized=$false
            docker_restart_authorized=$false
            docker_prune_authorized=$false
            vhdx_create_authorized=$false
            vhdx_delete_authorized=$false
            vhdx_move_authorized=$false
            wsl_shutdown_authorized=$false
            wsl_unmount_authorized=$false
            clickhouse_mutation_authorized=$false
            cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
        production_invariant_preserved=$true
        env_unchanged=$envUnchanged
    }
    $receipt | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== PRODUCTION REBALANCE PHASE2 D RESUMABLE APPLY RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "apply_accepted=$applyAccepted"
    Write-Host "mutation_performed=$mutationPerformed"
    Write-Host "authority_manifest_sha256=$($authority.sha256)"
    Write-Host "journal_path=$journalPath"
    Write-Host "protected_visual_processed_unchanged=$protectedUnchanged"
    Write-Host 'recursive_legacy_raw_root_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'
    Write-Host "Evidence directory: $([System.IO.Path]::GetDirectoryName($receiptPath))"
    Write-Host 'PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_DONE'
}
finally { Pop-Location }
