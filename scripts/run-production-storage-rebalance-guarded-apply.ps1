[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [ValidateSet('Phase1E','Phase2D')]
    [string]$Phase,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyRawRoot = 'D:\yoomarks\markorbit-data-engine\raw_data',
    [string]$RawTargetRoot = 'F:\MarkOrbitData\raw',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
    [string]$EvidenceRoot = 'reports',
    [switch]$AcknowledgeTemporary20Percent,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
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
        throw "$Command $($Arguments -join ' ') failed with exit code ${nativeExitCode}: $($renderedLines -join [Environment]::NewLine)"
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
    if ($headSha -ne $expected -or $originMainSha -ne $expected) {
        throw "Exact main drift detected during $Boundary."
    }
    if (git status --porcelain) { throw "Working tree must be clean during $Boundary." }
}

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    if (-not ([System.IO.Path]::IsPathRooted($Path) -and $Path -match '^[A-Za-z]:[\\/]')) { return '' }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
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

function Get-DriveSnapshot([string]$Letter) {
    $root = "${Letter}:\"
    if (-not (Test-Path -LiteralPath $root)) { throw "Required drive missing: $root" }
    $driveInfo = New-Object System.IO.DriveInfo($root)
    return [ordered]@{
        drive="${Letter}:"
        total_bytes=[int64]$driveInfo.TotalSize
        free_bytes=[int64]$driveInfo.AvailableFreeSpace
        filesystem=[string]$driveInfo.DriveFormat
    }
}

function Get-PathStats([string]$Path) {
    $normalized = Normalize-HostPath $Path
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized)) {
        return [ordered]@{
            path=$Path; normalized_path=$normalized; exists=$false; path_type=$null
            file_count=[int64]0; total_bytes=[int64]0; complete=$true; enumeration_error=$null
        }
    }
    if (Test-Path -LiteralPath $normalized -PathType Leaf) {
        $fileInfo = Get-Item -LiteralPath $normalized -Force
        return [ordered]@{
            path=$Path; normalized_path=$normalized; exists=$true; path_type='file'
            file_count=[int64]1; total_bytes=[int64]$fileInfo.Length; complete=$true; enumeration_error=$null
        }
    }
    $count = [int64]0
    $bytes = [int64]0
    try {
        foreach ($filePath in [System.IO.Directory]::EnumerateFiles($normalized, '*', [System.IO.SearchOption]::AllDirectories)) {
            $info = New-Object System.IO.FileInfo($filePath)
            $count++
            $bytes += [int64]$info.Length
        }
        return [ordered]@{
            path=$Path; normalized_path=$normalized; exists=$true; path_type='directory'
            file_count=$count; total_bytes=$bytes; complete=$true; enumeration_error=$null
        }
    }
    catch {
        return [ordered]@{
            path=$Path; normalized_path=$normalized; exists=$true; path_type='directory'
            file_count=$count; total_bytes=$bytes; complete=$false; enumeration_error=$_.Exception.Message
        }
    }
}

function Get-ProductionClickHouseHealth {
    $idProbe = Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse') -AllowFailure
    $ids = @($idProbe['lines'] | Where-Object { $_.Trim() })
    if ($idProbe['exit_code'] -ne 0 -or $ids.Count -ne 1) {
        return [ordered]@{ ready=$false; health=$null; container_id=$null }
    }
    $containerId = $ids[0].Trim()
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim().ToLowerInvariant()
    $ready = [bool](
        $healthProbe['exit_code'] -eq 0 -and
        $health -eq 'healthy' -and
        $sqlProbe['exit_code'] -eq 0 -and
        ((@($sqlProbe['lines']) -join '').Trim() -eq '1')
    )
    return [ordered]@{ ready=$ready; health=$health; container_id=$containerId }
}

function Get-AllContainerMounts {
    $idsProbe = Invoke-NativeText 'docker' @('ps','-a','-q') -AllowFailure
    if ($idsProbe['exit_code'] -ne 0) { throw 'Unable to enumerate Docker containers.' }
    $entries = @()
    foreach ($containerId in @($idsProbe['lines'] | Where-Object { $_.Trim() })) {
        $trimmedId = $containerId.Trim()
        $inspectProbe = Invoke-NativeText 'docker' @('inspect','--format','{{json .}}',$trimmedId) -AllowFailure
        if ($inspectProbe['exit_code'] -ne 0) { throw "Unable to inspect container $trimmedId." }
        $inspectJson = (@($inspectProbe['lines']) -join "`n").Trim()
        if (-not $inspectJson) { throw "Docker inspect produced no JSON for $trimmedId." }
        try { $container = $inspectJson | ConvertFrom-Json }
        catch { throw "Docker inspect produced invalid JSON for ${trimmedId}: $($_.Exception.Message)" }
        $state = Get-OptionalPropertyValue $container 'State'
        if ($null -eq $state) { throw "Docker inspect omitted State for $trimmedId." }
        $runningValue = Get-OptionalPropertyValue $state 'Running'
        if ($null -eq $runningValue) { throw "Docker inspect omitted State.Running for $trimmedId." }
        foreach ($mount in @(Get-OptionalArrayProperty $container 'Mounts')) {
            $source = [string](Get-OptionalPropertyValue $mount 'Source')
            $entries += [ordered]@{
                container_id=[string](Get-OptionalPropertyValue $container 'Id')
                container_name=([string](Get-OptionalPropertyValue $container 'Name')).TrimStart('/')
                running=[bool]$runningValue
                mount_type=[string](Get-OptionalPropertyValue $mount 'Type')
                source=$source
                normalized_source=(Normalize-HostPath $source)
                destination=[string](Get-OptionalPropertyValue $mount 'Destination')
                volume_name=[string](Get-OptionalPropertyValue $mount 'Name')
            }
        }
    }
    return @($entries)
}

function Get-ComposeBindMounts {
    $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','config','--format','json') -AllowFailure
    if ($probe['exit_code'] -ne 0) { throw 'Unable to resolve current Docker Compose model.' }
    try { $config = ((@($probe['lines']) -join "`n") | ConvertFrom-Json) }
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
            $entries += [ordered]@{
                service=[string]$serviceProperty.Name
                source=$source
                normalized_source=(Normalize-HostPath $source)
                target=$target
            }
        }
    }
    return @($entries)
}

function Get-PathReferences([string]$CandidatePath, [object[]]$ContainerMounts, [object[]]$ComposeBinds) {
    $allContainer = @($ContainerMounts | Where-Object { $_.normalized_source -and (Test-PathsOverlap $CandidatePath $_.normalized_source) })
    $runningContainer = @($allContainer | Where-Object { [bool]$_.running })
    $compose = @($ComposeBinds | Where-Object { $_.normalized_source -and (Test-PathsOverlap $CandidatePath $_.normalized_source) })
    return [ordered]@{
        all_container_reference_count=$allContainer.Count
        running_container_reference_count=$runningContainer.Count
        compose_reference_count=$compose.Count
        all_container_references=@($allContainer)
        running_container_references=@($runningContainer)
        compose_references=@($compose)
    }
}

function Assert-RawConsumersStopped {
    $services = @('api','worker','mark-image-worker','qcc-acquisition')
    $runningTotal = 0
    foreach ($service in $services) {
        $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','ps','-a','-q',$service) -AllowFailure
        if ($probe['exit_code'] -ne 0) { throw "Unable to inspect Raw consumer service $service." }
        $runningCount = 0
        foreach ($containerId in @($probe['lines'] | Where-Object { $_.Trim() })) {
            $stateProbe = Invoke-NativeText 'docker' @('inspect','--format','{{.State.Running}}',$containerId.Trim()) -AllowFailure
            if ($stateProbe['exit_code'] -ne 0) { throw "Unable to inspect Raw consumer container for $service." }
            if (((@($stateProbe['lines']) -join '').Trim().ToLowerInvariant()) -eq 'true') { $runningCount++ }
        }
        $runningTotal += $runningCount
        Write-Host "raw_consumer_service=$service running_count=$runningCount"
    }
    Write-Host "running_raw_consumer_count=$runningTotal"
    if ($runningTotal -ne 0) { throw "All Raw consumer services must be absent/stopped; observed $runningTotal running containers." }
}

function Assert-AcceptedProductionMount([object[]]$ContainerMounts, [string]$ContainerId) {
    $matches = @($ContainerMounts | Where-Object {
        $_.container_id -eq $ContainerId -and $_.destination -eq '/var/lib/clickhouse'
    })
    $ready = [bool](
        $matches.Count -eq 1 -and
        [string]$matches[0].mount_type -eq 'volume' -and
        [string]$matches[0].volume_name -eq $AcceptedVolume
    )
    Write-Host "accepted_production_mount_ready=$ready"
    if (-not $ready) { throw 'Production ClickHouse data mount is not the accepted named volume.' }
}

function Assert-NoReparsePoints([string]$Root) {
    $normalized = Normalize-HostPath $Root
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized)) { return }
    $rootAttributes = [System.IO.File]::GetAttributes($normalized)
    if (($rootAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Reparse-point root is not eligible for guarded deletion: $normalized"
    }
    if (($rootAttributes -band [System.IO.FileAttributes]::Directory) -eq 0) { return }
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($normalized)
    while ($stack.Count -gt 0) {
        $directory = $stack.Pop()
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
            $attributes = [System.IO.File]::GetAttributes($entry)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse point found inside guarded deletion tree: $entry"
            }
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { $stack.Push($entry) }
        }
    }
}

function Get-ResidualDeficitBytes {
    param([int64]$RequiredFreeBytes, [int64]$ObservedFreeBytes)
    if ($RequiredFreeBytes -lt 0 -or $ObservedFreeBytes -lt 0) { throw 'Residual deficit inputs must be non-negative.' }
    return [int64][math]::Max([int64]0, [int64]($RequiredFreeBytes - $ObservedFreeBytes))
}

function Invoke-FreshCandidateInventory([string]$RunId) {
    if ($RunId -notmatch '^[0-9A-Za-z_-]+$') { throw 'RunId contains unsupported characters.' }
    $childRelativeRoot = Join-Path (Join-Path 'reports' '_ra') $RunId
    $childAbsoluteRoot = Join-Path $repoRoot $childRelativeRoot
    New-Item -ItemType Directory -Force -Path $childAbsoluteRoot | Out-Null
    Write-Host 'guarded_apply_stage=fresh_candidate_inventory'
    Write-Host 'candidate_inventory_evidence_strategy=SHALLOW_REPO_REPORTS'
    Write-Host "candidate_inventory_evidence_root=$childAbsoluteRoot"
    $scriptPath = Join-Path $PSScriptRoot 'profile-production-storage-rebalance-candidates.ps1'
    $childArgs = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-AcceptedVolume',$AcceptedVolume,
        '-LegacyRawRoot',$LegacyRawRoot,
        '-RawTargetRoot',$RawTargetRoot,
        '-LegacyEHotRoot',$LegacyEHotRoot,
        '-LegacyEHotLogsRoot',$LegacyEHotLogsRoot,
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
    $directories = @(Get-ChildItem -LiteralPath $childAbsoluteRoot -Directory -Filter 'production_storage_rebalance_inventory_*' | Sort-Object LastWriteTime -Descending)
    if ($directories.Count -ne 1) { throw "Expected exactly one isolated rebalance inventory directory; observed $($directories.Count)." }
    $reportPath = Join-Path $directories[0].FullName 'production_storage_rebalance_candidate_inventory.json'
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) { throw 'Fresh rebalance candidate inventory receipt is missing.' }
    try { $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Fresh rebalance candidate inventory receipt is invalid JSON: $($_.Exception.Message)" }
    return [ordered]@{ path=$reportPath; report=$report; evidence_root=$childAbsoluteRoot }
}

function Find-AcceptedApplyPlanReceipt {
    $reportsRoot = Join-Path $repoRoot 'reports'
    if (-not (Test-Path -LiteralPath $reportsRoot -PathType Container)) { throw 'reports directory is missing; accepted apply-plan evidence is required.' }
    $candidates = @(Get-ChildItem -LiteralPath $reportsRoot -Directory -Filter 'production_storage_rebalance_apply_plan_*' | Sort-Object LastWriteTime -Descending)
    foreach ($directory in $candidates) {
        $path = Join-Path $directory.FullName 'production_storage_rebalance_apply_plan.json'
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        try { $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { continue }
        if ([string]$receipt.plan_version -eq 'PRODUCTION_STORAGE_REBALANCE_APPLY_PLAN_V1' -and
            [string]$receipt.decision -eq 'PRODUCTION_REBALANCE_TEMPORARY_20_PERCENT_APPLY_PLAN_READY' -and
            [string]$receipt.next_gate -eq 'PRODUCTION_REBALANCE_GUARDED_APPLY_WITH_TEMPORARY_20_PERCENT_ACK' -and
            [bool]$receipt.temporary_20_percent_review_required) {
            return [ordered]@{ path=$path; receipt=$receipt }
        }
    }
    throw 'No accepted PRODUCTION_STORAGE_REBALANCE_APPLY_PLAN_V1 receipt found under reports.'
}

function Find-AcceptedPhase1Receipt {
    $reportsRoot = Join-Path $repoRoot 'reports'
    if (-not (Test-Path -LiteralPath $reportsRoot -PathType Container)) { throw 'reports directory is missing; accepted Phase1E evidence is required.' }
    $candidates = @(Get-ChildItem -LiteralPath $reportsRoot -Directory -Filter 'production_storage_rebalance_guarded_apply_*' | Sort-Object LastWriteTime -Descending)
    foreach ($directory in $candidates) {
        $path = Join-Path $directory.FullName 'production_storage_rebalance_guarded_apply.json'
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        try { $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { continue }
        if ([string]$receipt.receipt_version -eq 'PRODUCTION_STORAGE_REBALANCE_GUARDED_APPLY_V1' -and
            [string]$receipt.phase -eq 'PHASE1_E' -and
            [string]$receipt.decision -eq 'PRODUCTION_REBALANCE_PHASE1_E_GO' -and
            [bool]$receipt.apply_accepted -and
            [string]$receipt.engine_sha -eq $ExpectedMainSha.Trim().ToLowerInvariant()) {
            return [ordered]@{ path=$path; receipt=$receipt }
        }
    }
    throw 'No accepted same-main PHASE1_E guarded-apply receipt found under reports.'
}

function Assert-CommonInventoryContract([object]$Inventory) {
    if ($null -eq $Inventory) { throw 'Fresh rebalance candidate inventory is empty.' }
    if ([string]$Inventory.receipt_version -ne 'PRODUCTION_STORAGE_REBALANCE_CANDIDATE_INVENTORY_V1') { throw 'Unexpected rebalance candidate receipt version.' }
    if ([string]$Inventory.decision -ne 'REBALANCE_TEMPORARY_HARD_FLOOR_CANDIDATES_FOUND') {
        throw "Guarded temporary-20-percent apply requires REBALANCE_TEMPORARY_HARD_FLOOR_CANDIDATES_FOUND; observed $($Inventory.decision)."
    }
    if (-not [bool]$Inventory.read_only -or -not [bool]$Inventory.production_invariant_preserved -or -not [bool]$Inventory.env_unchanged) {
        throw 'Fresh candidate inventory did not preserve read-only production invariants.'
    }
    if (-not [bool]$Inventory.production.accepted_production_mount_ready -or
        [string]$Inventory.production.accepted_volume -ne $AcceptedVolume -or
        [int64]$Inventory.production.worker_container_count -ne 0) {
        throw 'Fresh candidate inventory did not prove the accepted production named-volume boundary.'
    }
    $dCandidate = $Inventory.preferred_candidates.D.legacy_raw
    if (-not [bool]$dCandidate.preferred_candidate -or
        -not [bool]$dCandidate.metadata_parity_exact -or
        [int64]$dCandidate.unexpected_compose_reference_count -ne 0 -or
        [int64]$dCandidate.unexpected_container_reference_count -ne 0 -or
        -not [bool]$Inventory.preferred_candidates.D.hard_deficit_covered -or
        [bool]$Inventory.preferred_candidates.D.recommended_deficit_covered) {
        throw 'D legacy Raw no longer satisfies the temporary-20-percent duplicate-source contract.'
    }
}

function Assert-ComposeRawBindings([object[]]$ComposeBinds) {
    $rawServices = @('api','worker','mark-image-worker','qcc-acquisition')
    foreach ($service in $rawServices) {
        $raw = @($ComposeBinds | Where-Object { $_.service -eq $service -and $_.target -eq '/data/raw' })
        if ($raw.Count -ne 1 -or -not (Normalize-HostPath $raw[0].normalized_source).Equals((Normalize-HostPath $RawTargetRoot), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Compose /data/raw binding for $service does not resolve exactly to accepted F Raw target."
        }
    }
    foreach ($service in @('api','worker','mark-image-worker')) {
        $visualRaw = @($ComposeBinds | Where-Object { $_.service -eq $service -and $_.target -eq '/data/visual-raw' })
        if ($visualRaw.Count -ne 1 -or -not (Normalize-HostPath $visualRaw[0].normalized_source).Equals((Normalize-HostPath $RawTargetRoot), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Compose /data/visual-raw binding for $service does not resolve exactly to accepted F Raw target."
        }
        $visualProcessed = @($ComposeBinds | Where-Object { $_.service -eq $service -and $_.target -eq '/data/visual-processed' })
        $expectedProtected = Normalize-HostPath (Join-Path $LegacyRawRoot 'visual_processed')
        if ($visualProcessed.Count -ne 1 -or -not (Normalize-HostPath $visualProcessed[0].normalized_source).Equals($expectedProtected, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Compose /data/visual-processed binding for $service does not resolve exactly to the protected D subtree."
        }
    }
}

function Get-RawDeletionManifest([string]$SourceRoot, [string]$ProtectedRoot) {
    $source = Normalize-HostPath $SourceRoot
    $protected = Normalize-HostPath $ProtectedRoot
    if (-not $source -or -not (Test-Path -LiteralPath $source -PathType Container)) { throw 'Legacy D Raw source directory is missing.' }
    if (-not $protected -or -not (Test-PathContains $source $protected)) { throw 'Protected visual_processed path is not a child of the legacy D Raw root.' }
    $prefix = $source + '\'
    $entries = @()
    foreach ($filePath in [System.IO.Directory]::EnumerateFiles($source, '*', [System.IO.SearchOption]::AllDirectories)) {
        $fullPath = [System.IO.Path]::GetFullPath($filePath)
        if (Test-PathContains $protected $fullPath) { continue }
        $info = New-Object System.IO.FileInfo($fullPath)
        $entries += [pscustomobject]@{
            relative_path=$fullPath.Substring($prefix.Length)
            source_path=$fullPath
            length=[int64]$info.Length
            last_write_utc_ticks=[int64]$info.LastWriteTimeUtc.Ticks
        }
    }
    return @($entries | Sort-Object relative_path)
}

function Compare-RawMetadataManifests([object[]]$Left, [object[]]$Right) {
    if (@($Left).Count -ne @($Right).Count) { return $false }
    for ($index = 0; $index -lt @($Left).Count; $index++) {
        $leftEntry = $Left[$index]
        $rightEntry = $Right[$index]
        if (-not ([string]$leftEntry.relative_path).Equals([string]$rightEntry.relative_path, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        if ([int64]$leftEntry.length -ne [int64]$rightEntry.length) { return $false }
        if ([int64]$leftEntry.last_write_utc_ticks -ne [int64]$rightEntry.last_write_utc_ticks) { return $false }
    }
    return $true
}

function Get-ProtectedStats([string]$ProtectedRoot) {
    $stats = Get-PathStats $ProtectedRoot
    if (-not [bool]$stats.complete) { throw "Protected subtree inventory incomplete: $($stats.enumeration_error)" }
    return $stats
}

try {
    Write-Host '===== PRODUCTION STORAGE REBALANCE GUARDED APPLY ====='
    Write-Host "requested_phase=$Phase"
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "temporary_20_percent_acknowledged=$([bool]$AcknowledgeTemporary20Percent)"

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Production storage rebalance guarded apply must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Production storage rebalance guarded apply requires elevated Administrator PowerShell.'
    }
    if ($Apply -and -not $AcknowledgeTemporary20Percent) {
        throw '-Apply requires explicit -AcknowledgeTemporary20Percent.'
    }

    $legacyRawNormalized = Normalize-HostPath $LegacyRawRoot
    $rawTargetNormalized = Normalize-HostPath $RawTargetRoot
    $legacyEHotNormalized = Normalize-HostPath $LegacyEHotRoot
    $legacyELogsNormalized = Normalize-HostPath $LegacyEHotLogsRoot
    $protectedVisualProcessed = Normalize-HostPath (Join-Path $LegacyRawRoot 'visual_processed')
    if (-not $legacyRawNormalized.Equals('D:\yoomarks\markorbit-data-engine\raw_data', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyRawRoot must remain the exact approved D Raw root.' }
    if (-not $rawTargetNormalized.Equals('F:\MarkOrbitData\raw', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RawTargetRoot must remain the exact accepted F Raw root.' }
    if (-not $legacyEHotNormalized.Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotRoot must remain the exact approved legacy E ClickHouse root.' }
    if (-not $legacyELogsNormalized.Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotLogsRoot must remain the exact approved legacy E ClickHouse log root.' }

    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $runId = "${timestamp}_$PID"
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_storage_rebalance_guarded_apply_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

    $acceptedPlan = Find-AcceptedApplyPlanReceipt
    Write-Host "accepted_apply_plan_receipt=$($acceptedPlan['path'])"
    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_before=$([bool]$productionBefore['ready'])"
    Write-Host "production_clickhouse_health_before=$($productionBefore['health'])"
    if (-not $productionBefore['ready']) { throw 'Production ClickHouse must be healthy before guarded apply.' }

    $inventoryResult = Invoke-FreshCandidateInventory $runId
    $inventory = $inventoryResult['report']
    Assert-CommonInventoryContract $inventory
    $containerMountsBefore = @(Get-AllContainerMounts)
    $composeBindsBefore = @(Get-ComposeBindMounts)
    Assert-AcceptedProductionMount $containerMountsBefore $productionBefore['container_id']
    Assert-ComposeRawBindings $composeBindsBefore

    if ($Phase -eq 'Phase1E') {
        Write-Host 'guarded_apply_stage=phase1_e_preflight'
        $eHotCandidate = $inventory.preferred_candidates.E.legacy_ntfs_clickhouse
        $eLogsCandidate = $inventory.preferred_candidates.E.legacy_ntfs_clickhouse_logs
        $eHotStats = Get-PathStats $legacyEHotNormalized
        $eLogsStats = Get-PathStats $legacyELogsNormalized
        if (-not [bool]$eHotStats.complete -or -not [bool]$eLogsStats.complete) { throw 'Legacy E candidate enumeration is incomplete.' }
        if ([bool]$eHotStats.exists -and -not [bool]$eHotCandidate.preferred_candidate) { throw 'Existing legacy E ClickHouse root is no longer a preferred unreferenced candidate.' }
        if ([bool]$eLogsStats.exists -and [int64]$eLogsStats.total_bytes -gt 0 -and -not [bool]$eLogsCandidate.preferred_candidate) { throw 'Existing legacy E ClickHouse log root is no longer a preferred unreferenced candidate.' }
        if ([bool]$eHotStats.exists -and [int64]$eHotStats.total_bytes -ne [int64]$eHotCandidate.stats.total_bytes) { throw 'Legacy E ClickHouse bytes changed after fresh candidate inventory.' }
        if ([bool]$eLogsStats.exists -and [int64]$eLogsStats.total_bytes -ne [int64]$eLogsCandidate.stats.total_bytes) { throw 'Legacy E ClickHouse log bytes changed after fresh candidate inventory.' }

        $eHotRefs = Get-PathReferences $legacyEHotNormalized $containerMountsBefore $composeBindsBefore
        $eLogsRefs = Get-PathReferences $legacyELogsNormalized $containerMountsBefore $composeBindsBefore
        Write-Host "phase1_e_hot_container_reference_count=$($eHotRefs['all_container_reference_count'])"
        Write-Host "phase1_e_hot_compose_reference_count=$($eHotRefs['compose_reference_count'])"
        Write-Host "phase1_e_logs_container_reference_count=$($eLogsRefs['all_container_reference_count'])"
        Write-Host "phase1_e_logs_compose_reference_count=$($eLogsRefs['compose_reference_count'])"
        if ($eHotRefs['all_container_reference_count'] -ne 0 -or $eHotRefs['compose_reference_count'] -ne 0 -or
            $eLogsRefs['all_container_reference_count'] -ne 0 -or $eLogsRefs['compose_reference_count'] -ne 0) {
            throw 'Legacy E ClickHouse/log roots gained a Docker or Compose reference.'
        }
        Assert-NoReparsePoints $legacyEHotNormalized
        Assert-NoReparsePoints $legacyELogsNormalized

        $driveEBefore = Get-DriveSnapshot 'E'
        $requiredERecommendedFree = [int64]([int64]$inventory.drives.E.free_bytes + [int64]$inventory.deficits.e_additional_free_recommended_bytes)
        $plannedEBytes = [int64]([int64]$eHotStats.total_bytes + [int64]$eLogsStats.total_bytes)
        Write-Host "phase1_e_planned_delete_bytes=$plannedEBytes"
        Write-Host "phase1_e_required_recommended_free_bytes=$requiredERecommendedFree"
        Write-Host "phase1_e_free_before_bytes=$($driveEBefore['free_bytes'])"

        $phaseReady = [bool](
            (([bool]$eHotStats.exists -or [bool]$eLogsStats.exists) -or [int64]$inventory.deficits.e_additional_free_recommended_bytes -eq 0) -and
            [int64]$driveEBefore['free_bytes'] -le [int64]$driveEBefore['total_bytes']
        )
        if (-not $phaseReady) { throw 'Phase1E preflight is not ready.' }

        if (-not $Apply) {
            $decision = 'PRODUCTION_REBALANCE_PHASE1_E_READY_FOR_APPLY'
            $applyAccepted = $false
            $mutationPerformed = $false
            $driveEAfter = $driveEBefore
        }
        else {
            Write-Host 'guarded_apply_stage=phase1_e_delete_exact_legacy_roots'
            Assert-ExactMain 'phase1_e_before_delete'
            Assert-RawConsumersStopped
            $preDeleteMounts = @(Get-AllContainerMounts)
            $preDeleteCompose = @(Get-ComposeBindMounts)
            $preDeleteProduction = Get-ProductionClickHouseHealth
            if (-not $preDeleteProduction['ready']) { throw 'Production ClickHouse lost health immediately before Phase1E deletion.' }
            Assert-AcceptedProductionMount $preDeleteMounts $preDeleteProduction['container_id']
            if ((Get-PathReferences $legacyEHotNormalized $preDeleteMounts $preDeleteCompose)['all_container_reference_count'] -ne 0 -or
                (Get-PathReferences $legacyEHotNormalized $preDeleteMounts $preDeleteCompose)['compose_reference_count'] -ne 0 -or
                (Get-PathReferences $legacyELogsNormalized $preDeleteMounts $preDeleteCompose)['all_container_reference_count'] -ne 0 -or
                (Get-PathReferences $legacyELogsNormalized $preDeleteMounts $preDeleteCompose)['compose_reference_count'] -ne 0) {
                throw 'Legacy E roots gained a reference at the destructive boundary.'
            }

            if (Test-Path -LiteralPath $legacyEHotNormalized -PathType Container) {
                Remove-Item -LiteralPath $legacyEHotNormalized -Recurse -Force
            }
            if (Test-Path -LiteralPath $legacyELogsNormalized -PathType Container) {
                Remove-Item -LiteralPath $legacyELogsNormalized -Recurse -Force
            }
            if (Test-Path -LiteralPath $legacyEHotNormalized) { throw 'Legacy E ClickHouse root remains after exact Phase1E deletion.' }
            if (Test-Path -LiteralPath $legacyELogsNormalized) { throw 'Legacy E ClickHouse log root remains after exact Phase1E deletion.' }

            $driveEAfter = Get-DriveSnapshot 'E'
            if ([int64]$driveEAfter['free_bytes'] -lt $requiredERecommendedFree) {
                throw "Phase1E completed deletion but E free space is below the required recommended floor: $($driveEAfter['free_bytes']) < $requiredERecommendedFree."
            }
            Assert-RawConsumersStopped
            $productionAfterPhase = Get-ProductionClickHouseHealth
            if (-not $productionAfterPhase['ready']) { throw 'Production ClickHouse lost health after Phase1E deletion.' }
            $postMounts = @(Get-AllContainerMounts)
            Assert-AcceptedProductionMount $postMounts $productionAfterPhase['container_id']
            $decision = 'PRODUCTION_REBALANCE_PHASE1_E_GO'
            $applyAccepted = $true
            $mutationPerformed = [bool]($plannedEBytes -gt 0)
        }

        $envHashAfterPhase = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
        if ($envHashBefore -ne $envHashAfterPhase) { throw '.env changed during Phase1E guarded apply.' }
        $receipt = [ordered]@{
            receipt_version='PRODUCTION_STORAGE_REBALANCE_GUARDED_APPLY_V1'
            engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
            phase='PHASE1_E'
            decision=$decision
            next_gate=if ($applyAccepted) { 'PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_APPLY' } else { 'PRODUCTION_REBALANCE_PHASE1_E_APPLY' }
            temporary_20_percent_acknowledged=[bool]$AcknowledgeTemporary20Percent
            apply_requested=[bool]$Apply
            apply_accepted=$applyAccepted
            mutation_performed=$mutationPerformed
            accepted_apply_plan_receipt_path=$acceptedPlan['path']
            fresh_candidate_receipt_path=$inventoryResult['path']
            e=[ordered]@{
                hot_path=$legacyEHotNormalized
                logs_path=$legacyELogsNormalized
                planned_delete_bytes=$plannedEBytes
                free_before_bytes=[int64]$driveEBefore['free_bytes']
                free_after_bytes=[int64]$driveEAfter['free_bytes']
                required_recommended_free_bytes=$requiredERecommendedFree
                recommended_residual_after_bytes=(Get-ResidualDeficitBytes $requiredERecommendedFree ([int64]$driveEAfter['free_bytes']))
                hot_reference_count_before=[int64]$eHotRefs['all_container_reference_count']
                hot_compose_reference_count_before=[int64]$eHotRefs['compose_reference_count']
                logs_reference_count_before=[int64]$eLogsRefs['all_container_reference_count']
                logs_compose_reference_count_before=[int64]$eLogsRefs['compose_reference_count']
            }
            constraints=[ordered]@{
                phase1_e_exact_delete_authorized=[bool]($Apply -and $AcknowledgeTemporary20Percent -and $phaseReady)
                phase2_d_raw_delete_authorized=$false
                visual_processed_delete_authorized=$false
                accepted_volume_delete_authorized=$false
                accepted_volume_move_authorized=$false
                docker_cold_backup_delete_authorized=$false
                docker_prune_authorized=$false
                vhdx_create_authorized=$false
                wsl_shutdown_authorized=$false
                wsl_unmount_authorized=$false
                production_clickhouse_mutation_authorized=$false
                us_package_2_authorized=$false
                us_bulk_authorized=$false
            }
            env_unchanged=$true
            production_invariant_preserved=$true
        }
        $reportPath = Join-Path $evidenceDir 'production_storage_rebalance_guarded_apply.json'
        $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8
        Write-Host '===== PRODUCTION STORAGE REBALANCE GUARDED APPLY RESULT ====='
        Write-Host "phase=PHASE1_E"
        Write-Host "decision=$decision"
        Write-Host "next_gate=$($receipt.next_gate)"
        Write-Host "phase1_e_planned_delete_gib=$([math]::Round($plannedEBytes / 1GB, 2))"
        Write-Host "phase1_e_free_after_gib=$([math]::Round([int64]$driveEAfter['free_bytes'] / 1GB, 2))"
        Write-Host "phase1_e_recommended_residual_after_gib=$([math]::Round([int64]$receipt.e.recommended_residual_after_bytes / 1GB, 2))"
        Write-Host "apply_requested=$([bool]$Apply)"
        Write-Host "apply_accepted=$applyAccepted"
        Write-Host "mutation_performed=$mutationPerformed"
        Write-Host 'phase2_d_raw_delete_authorized=False'
        Write-Host 'visual_processed_delete_authorized=False'
        Write-Host 'accepted_volume_delete_authorized=False'
        Write-Host 'vhdx_create_authorized=False'
        Write-Host 'us_package_2_authorized=False'
        Write-Host 'us_bulk_authorized=False'
        Write-Host "Evidence directory: $evidenceDir"
    }
    else {
        Write-Host 'guarded_apply_stage=phase2_d_preflight'
        $acceptedPhase1 = Find-AcceptedPhase1Receipt
        Write-Host "accepted_phase1_receipt=$($acceptedPhase1['path'])"
        $dCandidate = $inventory.preferred_candidates.D.legacy_raw
        if ([int64]$inventory.deficits.e_additional_free_recommended_bytes -ne 0) {
            throw 'Phase2D requires E recommended coexistence deficit to be zero after accepted Phase1E.'
        }
        if ([bool]$inventory.preferred_candidates.E.legacy_ntfs_clickhouse.stats.exists -or
            [bool]$inventory.preferred_candidates.E.legacy_ntfs_clickhouse_logs.stats.exists) {
            throw 'Phase2D requires both legacy E ClickHouse roots to remain absent after accepted Phase1E.'
        }
        if (-not [bool]$dCandidate.preferred_candidate -or -not [bool]$dCandidate.metadata_parity_exact) {
            throw 'Phase2D legacy D Raw candidate is no longer eligible.'
        }
        Assert-NoReparsePoints $legacyRawNormalized
        Assert-NoReparsePoints $rawTargetNormalized
        $protectedBefore = Get-ProtectedStats $protectedVisualProcessed
        $driveDBefore = Get-DriveSnapshot 'D'
        $requiredDHardFree = [int64]([int64]$inventory.drives.D.free_bytes + [int64]$inventory.deficits.d_additional_free_hard_bytes)
        $requiredDRecommendedFree = [int64]([int64]$inventory.drives.D.free_bytes + [int64]$inventory.deficits.d_additional_free_recommended_bytes)

        Write-Host 'guarded_apply_stage=phase2_d_full_sha256_parity'
        $sourceManifestBefore = @(Get-RawDeletionManifest $legacyRawNormalized $protectedVisualProcessed)
        $sourceFileCount = $sourceManifestBefore.Count
        $sourceBytes = [int64](($sourceManifestBefore | Measure-Object -Property length -Sum).Sum)
        if ($null -eq $sourceBytes) { $sourceBytes = [int64]0 }
        Write-Host "phase2_d_deletable_file_count=$sourceFileCount"
        Write-Host "phase2_d_deletable_bytes=$sourceBytes"
        if ($sourceFileCount -eq 0) { throw 'Phase2D found no deletable legacy D Raw files.' }
        if ($sourceBytes -lt [int64]$inventory.deficits.d_additional_free_hard_bytes) {
            throw 'Phase2D deletable legacy D Raw bytes no longer cover the D hard-floor deficit.'
        }

        $verifiedEntries = @()
        $hashMismatchCount = 0
        $verifiedBytes = [int64]0
        $index = 0
        foreach ($entry in $sourceManifestBefore) {
            $index++
            $targetPath = Join-Path $rawTargetNormalized ([string]$entry.relative_path)
            if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) { throw "F Raw counterpart missing: $($entry.relative_path)" }
            $targetInfo = Get-Item -LiteralPath $targetPath -Force
            if ([int64]$targetInfo.Length -ne [int64]$entry.length) { throw "F Raw counterpart size mismatch: $($entry.relative_path)" }
            $sourceHash = (Get-FileHash -LiteralPath $entry.source_path -Algorithm SHA256).Hash.ToLowerInvariant()
            $targetHash = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($sourceHash -ne $targetHash) { $hashMismatchCount++ }
            else { $verifiedBytes += [int64]$entry.length }
            $verifiedEntries += [pscustomobject]@{
                relative_path=[string]$entry.relative_path
                source_path=[string]$entry.source_path
                target_path=[string]$targetPath
                length=[int64]$entry.length
                source_sha256=$sourceHash
                target_sha256=$targetHash
                hash_equal=[bool]($sourceHash -eq $targetHash)
            }
            if (($index % 100) -eq 0 -or $index -eq $sourceFileCount) {
                Write-Host "phase2_d_hash_progress=$index/$sourceFileCount"
            }
        }
        Write-Host "phase2_d_hash_mismatch_count=$hashMismatchCount"
        Write-Host "phase2_d_verified_bytes=$verifiedBytes"
        if ($hashMismatchCount -ne 0 -or $verifiedBytes -ne $sourceBytes) { throw 'Phase2D full SHA256 parity failed.' }

        $sourceManifestAfterHash = @(Get-RawDeletionManifest $legacyRawNormalized $protectedVisualProcessed)
        $sourceManifestStable = Compare-RawMetadataManifests $sourceManifestBefore $sourceManifestAfterHash
        Write-Host "phase2_d_source_manifest_stable=$sourceManifestStable"
        if (-not $sourceManifestStable) { throw 'Legacy D Raw manifest changed during SHA256 verification.' }
        Assert-RawConsumersStopped
        $preDeleteProduction = Get-ProductionClickHouseHealth
        if (-not $preDeleteProduction['ready']) { throw 'Production ClickHouse lost health after Phase2D SHA256 verification.' }
        $preDeleteMounts = @(Get-AllContainerMounts)
        $preDeleteCompose = @(Get-ComposeBindMounts)
        Assert-AcceptedProductionMount $preDeleteMounts $preDeleteProduction['container_id']
        Assert-ComposeRawBindings $preDeleteCompose
        $dRefs = Get-PathReferences $legacyRawNormalized $preDeleteMounts $preDeleteCompose
        $unexpectedDContainerRefs = @($dRefs['all_container_references'] | Where-Object { -not (Test-PathContains $protectedVisualProcessed $_.normalized_source) })
        $unexpectedDComposeRefs = @($dRefs['compose_references'] | Where-Object { -not (Test-PathContains $protectedVisualProcessed $_.normalized_source) })
        Write-Host "phase2_d_unexpected_container_reference_count=$($unexpectedDContainerRefs.Count)"
        Write-Host "phase2_d_unexpected_compose_reference_count=$($unexpectedDComposeRefs.Count)"
        if ($unexpectedDContainerRefs.Count -ne 0 -or $unexpectedDComposeRefs.Count -ne 0) { throw 'Legacy D Raw gained an unexpected reference before deletion.' }

        $verifiedManifestPath = Join-Path $evidenceDir 'phase2_d_verified_sha256_manifest.json'
        @($verifiedEntries) | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $verifiedManifestPath -Encoding UTF8
        if (-not $Apply) {
            $decision = 'PRODUCTION_REBALANCE_PHASE2_D_READY_FOR_APPLY'
            $applyAccepted = $false
            $mutationPerformed = $false
            $driveDAfter = $driveDBefore
        }
        else {
            Write-Host 'guarded_apply_stage=phase2_d_delete_verified_files_individually'
            Assert-ExactMain 'phase2_d_before_delete'
            foreach ($entry in $verifiedEntries) {
                if (-not [bool]$entry.hash_equal) { throw "Unverified manifest entry reached deletion boundary: $($entry.relative_path)" }
                if (Test-PathContains $protectedVisualProcessed $entry.source_path) { throw "Protected visual_processed file reached deletion boundary: $($entry.relative_path)" }
                if (-not (Test-Path -LiteralPath $entry.source_path -PathType Leaf)) { throw "Verified source file disappeared before deletion: $($entry.relative_path)" }
                Remove-Item -LiteralPath $entry.source_path -Force
            }
            $remainingManifest = @(Get-RawDeletionManifest $legacyRawNormalized $protectedVisualProcessed)
            if ($remainingManifest.Count -ne 0) { throw "Phase2D left $($remainingManifest.Count) deletable legacy D Raw files behind." }
            $protectedAfter = Get-ProtectedStats $protectedVisualProcessed
            if ([bool]$protectedBefore.exists -ne [bool]$protectedAfter.exists -or
                [int64]$protectedBefore.file_count -ne [int64]$protectedAfter.file_count -or
                [int64]$protectedBefore.total_bytes -ne [int64]$protectedAfter.total_bytes) {
                throw 'Protected visual_processed subtree changed during Phase2D deletion.'
            }
            $driveDAfter = Get-DriveSnapshot 'D'
            if ([int64]$driveDAfter['free_bytes'] -lt $requiredDHardFree) {
                throw "Phase2D completed verified deletion but D free space is below the required 20-percent hard-floor coexistence target: $($driveDAfter['free_bytes']) < $requiredDHardFree."
            }
            Assert-RawConsumersStopped
            $productionAfterPhase = Get-ProductionClickHouseHealth
            if (-not $productionAfterPhase['ready']) { throw 'Production ClickHouse lost health after Phase2D deletion.' }
            $postMounts = @(Get-AllContainerMounts)
            Assert-AcceptedProductionMount $postMounts $productionAfterPhase['container_id']
            $decision = 'PRODUCTION_REBALANCE_PHASE2_D_GO'
            $applyAccepted = $true
            $mutationPerformed = $true
        }

        $envHashAfterPhase = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
        if ($envHashBefore -ne $envHashAfterPhase) { throw '.env changed during Phase2D guarded apply.' }
        $hardResidualAfter = Get-ResidualDeficitBytes $requiredDHardFree ([int64]$driveDAfter['free_bytes'])
        $recommendedResidualAfter = Get-ResidualDeficitBytes $requiredDRecommendedFree ([int64]$driveDAfter['free_bytes'])
        $receipt = [ordered]@{
            receipt_version='PRODUCTION_STORAGE_REBALANCE_GUARDED_APPLY_V1'
            engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
            phase='PHASE2_D'
            decision=$decision
            next_gate=if ($applyAccepted) { 'PRODUCTION_HOT_WARM_SIZING_REFRESH_AFTER_REBALANCE' } else { 'PRODUCTION_REBALANCE_PHASE2_D_APPLY' }
            temporary_20_percent_acknowledged=[bool]$AcknowledgeTemporary20Percent
            apply_requested=[bool]$Apply
            apply_accepted=$applyAccepted
            mutation_performed=$mutationPerformed
            accepted_apply_plan_receipt_path=$acceptedPlan['path']
            accepted_phase1_receipt_path=$acceptedPhase1['path']
            fresh_candidate_receipt_path=$inventoryResult['path']
            verified_sha256_manifest_path=$verifiedManifestPath
            d=[ordered]@{
                source_root=$legacyRawNormalized
                target_root=$rawTargetNormalized
                protected_visual_processed=$protectedVisualProcessed
                deletable_file_count=$sourceFileCount
                deletable_bytes=$sourceBytes
                verified_file_count=@($verifiedEntries | Where-Object { [bool]$_.hash_equal }).Count
                verified_bytes=$verifiedBytes
                hash_mismatch_count=$hashMismatchCount
                source_manifest_stable=$sourceManifestStable
                free_before_bytes=[int64]$driveDBefore['free_bytes']
                free_after_bytes=[int64]$driveDAfter['free_bytes']
                required_hard_free_bytes=$requiredDHardFree
                required_recommended_free_bytes=$requiredDRecommendedFree
                hard_residual_after_bytes=$hardResidualAfter
                recommended_residual_after_bytes=$recommendedResidualAfter
                unexpected_container_reference_count=$unexpectedDContainerRefs.Count
                unexpected_compose_reference_count=$unexpectedDComposeRefs.Count
            }
            constraints=[ordered]@{
                phase1_e_exact_delete_authorized=$false
                phase2_d_verified_file_delete_authorized=[bool]($Apply -and $AcknowledgeTemporary20Percent -and $hashMismatchCount -eq 0 -and $sourceManifestStable)
                recursive_legacy_raw_root_delete_authorized=$false
                visual_processed_delete_authorized=$false
                accepted_volume_delete_authorized=$false
                accepted_volume_move_authorized=$false
                docker_cold_backup_delete_authorized=$false
                docker_prune_authorized=$false
                vhdx_create_authorized=$false
                wsl_shutdown_authorized=$false
                wsl_unmount_authorized=$false
                production_clickhouse_mutation_authorized=$false
                us_package_2_authorized=$false
                us_bulk_authorized=$false
            }
            env_unchanged=$true
            production_invariant_preserved=$true
        }
        $reportPath = Join-Path $evidenceDir 'production_storage_rebalance_guarded_apply.json'
        $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8
        Write-Host '===== PRODUCTION STORAGE REBALANCE GUARDED APPLY RESULT ====='
        Write-Host 'phase=PHASE2_D'
        Write-Host "decision=$decision"
        Write-Host "next_gate=$($receipt.next_gate)"
        Write-Host "verified_file_count=$($receipt.d.verified_file_count)"
        Write-Host "verified_source_bytes=$verifiedBytes"
        Write-Host "hash_mismatch_count=$hashMismatchCount"
        Write-Host "source_manifest_stable=$sourceManifestStable"
        Write-Host "d_free_after_gib=$([math]::Round([int64]$driveDAfter['free_bytes'] / 1GB, 2))"
        Write-Host "d_hard_residual_after_gib=$([math]::Round($hardResidualAfter / 1GB, 2))"
        Write-Host "d_recommended_residual_after_gib=$([math]::Round($recommendedResidualAfter / 1GB, 2))"
        Write-Host "apply_requested=$([bool]$Apply)"
        Write-Host "apply_accepted=$applyAccepted"
        Write-Host "mutation_performed=$mutationPerformed"
        Write-Host 'recursive_legacy_raw_root_delete_authorized=False'
        Write-Host 'visual_processed_delete_authorized=False'
        Write-Host 'accepted_volume_delete_authorized=False'
        Write-Host 'vhdx_create_authorized=False'
        Write-Host 'us_package_2_authorized=False'
        Write-Host 'us_bulk_authorized=False'
        Write-Host "Evidence directory: $evidenceDir"
    }

    Write-Host 'PRODUCTION_STORAGE_REBALANCE_GUARDED_APPLY_DONE'
    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
