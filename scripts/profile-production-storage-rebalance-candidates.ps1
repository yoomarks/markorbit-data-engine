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
    [string]$DockerColdBackupRoot = 'E:\DockerDataBackup',
    [string]$EvidenceRoot = 'reports'
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
        throw "$Command $($Arguments -join ' ') failed with exit code $nativeExitCode`: $($renderedLines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$nativeExitCode; lines=@($renderedLines) }
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $headSha = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMainSha = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$headSha"
    Write-Host "origin/main=$originMainSha"
    Write-Host "expected=$expected"
    if ($headSha -ne $expected -or $originMainSha -ne $expected) { throw "Exact main drift detected during $Phase." }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}

function Assert-NoWorkerContainers {
    $probe = Invoke-NativeText 'docker' @('compose','ps','-a','-q','worker') -AllowFailure
    if ($probe['exit_code'] -ne 0) { throw 'Unable to inspect worker container state.' }
    $workerCount = @($probe['lines'] | Where-Object { $_.Trim() }).Count
    Write-Host "worker_container_count=$workerCount"
    if ($workerCount -ne 0) { throw "Worker containers must be absent at rebalance inventory boundary; observed $workerCount." }
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

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    if (-not ([System.IO.Path]::IsPathRooted($Path) -and $Path -match '^[A-Za-z]:[\\/]')) { return '' }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Get-DriveSnapshot([string]$Letter) {
    $root = "${Letter}:\"
    if (-not (Test-Path -LiteralPath $root)) { throw "Required drive missing: $root" }
    $driveInfo = [System.IO.DriveInfo]::new($root)
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
            path=$Path
            normalized_path=$normalized
            exists=$false
            path_type=$null
            file_count=[int64]0
            total_bytes=[int64]0
            complete=$true
            enumeration_error=$null
        }
    }

    if (Test-Path -LiteralPath $normalized -PathType Leaf) {
        $fileInfo = Get-Item -LiteralPath $normalized -Force
        return [ordered]@{
            path=$Path
            normalized_path=$normalized
            exists=$true
            path_type='file'
            file_count=[int64]1
            total_bytes=[int64]$fileInfo.Length
            complete=$true
            enumeration_error=$null
        }
    }

    $count = [int64]0
    $bytes = [int64]0
    $complete = $true
    $enumerationError = $null
    try {
        foreach ($filePath in [System.IO.Directory]::EnumerateFiles($normalized, '*', [System.IO.SearchOption]::AllDirectories)) {
            $info = New-Object System.IO.FileInfo($filePath)
            $count++
            $bytes += [int64]$info.Length
        }
    }
    catch {
        $complete = $false
        $enumerationError = $_.Exception.Message
    }
    return [ordered]@{
        path=$Path
        normalized_path=$normalized
        exists=$true
        path_type='directory'
        file_count=$count
        total_bytes=$bytes
        complete=$complete
        enumeration_error=$enumerationError
    }
}

function Get-TreeMetadataMap([string]$Root) {
    $normalized = Normalize-HostPath $Root
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized -PathType Container)) { return $null }
    $prefix = $normalized + '\'
    $map = @{}
    foreach ($filePath in [System.IO.Directory]::EnumerateFiles($normalized, '*', [System.IO.SearchOption]::AllDirectories)) {
        $fullPath = [System.IO.Path]::GetFullPath($filePath)
        $relative = $fullPath.Substring($prefix.Length).ToLowerInvariant()
        $info = New-Object System.IO.FileInfo($fullPath)
        $map[$relative] = [int64]$info.Length
    }
    return $map
}

function Compare-TreeMetadataExact([string]$LeftRoot, [string]$RightRoot) {
    $leftMap = Get-TreeMetadataMap $LeftRoot
    $rightMap = Get-TreeMetadataMap $RightRoot
    if ($null -eq $leftMap -or $null -eq $rightMap) { return $false }
    if ($leftMap.Count -ne $rightMap.Count) { return $false }
    foreach ($key in $leftMap.Keys) {
        if (-not $rightMap.ContainsKey($key)) { return $false }
        if ([int64]$leftMap[$key] -ne [int64]$rightMap[$key]) { return $false }
    }
    return $true
}

function Get-AllContainerMounts {
    $idsProbe = Invoke-NativeText 'docker' @('ps','-a','-q') -AllowFailure
    if ($idsProbe['exit_code'] -ne 0) { throw 'Unable to enumerate Docker containers.' }
    $entries = @()
    foreach ($containerId in @($idsProbe['lines'] | Where-Object { $_.Trim() })) {
        $inspectProbe = Invoke-NativeText 'docker' @('inspect',$containerId.Trim()) -AllowFailure
        if ($inspectProbe['exit_code'] -ne 0) { throw "Unable to inspect container $containerId." }
        $objects = @((@($inspectProbe['lines']) -join "`n") | ConvertFrom-Json)
        if ($objects.Count -ne 1) { throw "Unexpected Docker inspect shape for $containerId." }
        $container = $objects[0]
        foreach ($mount in @($container.Mounts)) {
            $source = [string]$mount.Source
            $normalizedSource = Normalize-HostPath $source
            $entries += [ordered]@{
                container_id=[string]$container.Id
                container_name=([string]$container.Name).TrimStart('/')
                running=[bool]$container.State.Running
                mount_type=[string]$mount.Type
                source=$source
                normalized_source=$normalizedSource
                destination=[string]$mount.Destination
                volume_name=[string]$mount.Name
            }
        }
    }
    return @($entries)
}

function Get-ComposeBindMounts {
    $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','config','--format','json') -AllowFailure
    if ($probe['exit_code'] -ne 0) { throw 'Unable to resolve current Docker Compose model.' }
    $config = ((@($probe['lines']) -join "`n") | ConvertFrom-Json)
    $entries = @()
    foreach ($serviceProperty in @($config.services.PSObject.Properties)) {
        foreach ($mount in @($serviceProperty.Value.volumes)) {
            if ([string]$mount.type -ne 'bind') { continue }
            $source = [string]$mount.source
            $entries += [ordered]@{
                service=[string]$serviceProperty.Name
                source=$source
                normalized_source=(Normalize-HostPath $source)
                target=[string]$mount.target
            }
        }
    }
    return @($entries)
}

function Test-PathContains([string]$ParentPath, [string]$ChildPath) {
    $parent = Normalize-HostPath $ParentPath
    $child = Normalize-HostPath $ChildPath
    if (-not $parent -or -not $child) { return $false }
    if ($child -eq $parent) { return $true }
    return $child.StartsWith($parent + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-PathReferences([string]$CandidatePath, [object[]]$ContainerMounts, [object[]]$ComposeBinds) {
    $allContainer = @($ContainerMounts | Where-Object { $_.normalized_source -and (Test-PathContains $CandidatePath $_.normalized_source) })
    $runningContainer = @($allContainer | Where-Object { [bool]$_.running })
    $compose = @($ComposeBinds | Where-Object { $_.normalized_source -and (Test-PathContains $CandidatePath $_.normalized_source) })
    return [ordered]@{
        all_container_reference_count=$allContainer.Count
        running_container_reference_count=$runningContainer.Count
        compose_reference_count=$compose.Count
        all_container_references=@($allContainer)
        running_container_references=@($runningContainer)
        compose_references=@($compose)
    }
}

function Get-ReserveBytes([int64]$TotalBytes, [double]$FreePercent) {
    return [int64][math]::Ceiling([double]$TotalBytes * ($FreePercent / 100.0))
}

function Get-AdditionalFreeRequired([int64]$CurrentFreeBytes, [int64]$ReserveBytes, [int64]$AllocationBytes) {
    return [int64][math]::Max([int64]0, [int64](($ReserveBytes + $AllocationBytes) - $CurrentFreeBytes))
}

function Invoke-FreshSizing([string]$InventoryRelativeRoot) {
    $sizingRelativeRoot = Join-Path $InventoryRelativeRoot 'sizing'
    $sizingAbsoluteRoot = Join-Path $repoRoot $sizingRelativeRoot
    New-Item -ItemType Directory -Force -Path $sizingAbsoluteRoot | Out-Null
    $scriptPath = Join-Path $PSScriptRoot 'plan-production-hot-warm-sizing.ps1'
    $childArgs = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-AcceptedVolume',$AcceptedVolume,
        '-EvidenceRoot',$sizingRelativeRoot
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $outputLines = @(& powershell.exe @childArgs 2>&1)
        $childExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    foreach ($line in @($outputLines | ForEach-Object { $_.ToString() })) { Write-Host $line }
    if ($childExitCode -ne 0) { throw "Fresh production sizing exited $childExitCode." }

    $directories = @(Get-ChildItem -LiteralPath $sizingAbsoluteRoot -Directory -Filter 'production_hot_warm_sizing_*' |
        Sort-Object LastWriteTime -Descending)
    if ($directories.Count -ne 1) { throw "Expected exactly one isolated sizing directory; observed $($directories.Count)." }
    $reportPath = Join-Path $directories[0].FullName 'production_hot_warm_sizing_plan.json'
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) { throw 'Fresh sizing report missing.' }
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    return [ordered]@{ path=$reportPath; report=$report }
}

try {
    Write-Host '===== PRODUCTION STORAGE REBALANCE CANDIDATE INVENTORY ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Production storage rebalance inventory must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Production storage rebalance inventory requires elevated Administrator PowerShell.'
    }

    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    Assert-NoWorkerContainers
    $productionBefore = Get-ProductionClickHouseHealth
    if (-not $productionBefore['ready']) { throw 'Production ClickHouse must be healthy before rebalance inventory.' }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $inventoryRelativeRoot = Join-Path $EvidenceRoot "production_storage_rebalance_inventory_$timestamp"
    $evidenceDir = Join-Path $repoRoot $inventoryRelativeRoot
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

    Write-Host 'rebalance_stage=fresh_sizing'
    $sizingResult = Invoke-FreshSizing $inventoryRelativeRoot
    $sizing = $sizingResult['report']
    if ([string]$sizing.plan_version -ne 'PRODUCTION_HOT_WARM_SIZING_PLAN_V1' -or
        [string]$sizing.decision -ne 'PRODUCTION_HOT_WARM_SIZING_PLAN_READY' -or
        [string]$sizing.next_gate -ne 'PRODUCTION_STORAGE_REBALANCE_PLAN' -or
        [string]$sizing.fit.coexistence_state -ne 'REBALANCE_REQUIRED_BEFORE_PROVISION' -or
        @($sizing.blockers).Count -ne 0) {
        throw 'Fresh sizing does not require/permit the rebalance inventory path.'
    }

    Write-Host 'rebalance_stage=current_references'
    $containerMounts = @(Get-AllContainerMounts)
    $composeBinds = @(Get-ComposeBindMounts)
    $productionDataMounts = @($containerMounts | Where-Object {
        $_.container_id -eq $productionBefore['container_id'] -and $_.destination -eq '/var/lib/clickhouse'
    })
    $acceptedProductionMountReady = [bool](
        $productionDataMounts.Count -eq 1 -and
        [string]$productionDataMounts[0].mount_type -eq 'volume' -and
        [string]$productionDataMounts[0].volume_name -eq $AcceptedVolume
    )
    Write-Host "accepted_production_mount_ready=$acceptedProductionMountReady"
    if (-not $acceptedProductionMountReady) { throw 'Current production ClickHouse data mount is not the accepted named volume.' }

    Write-Host 'rebalance_stage=drive_and_candidate_inventory'
    $driveD = Get-DriveSnapshot 'D'
    $driveE = Get-DriveSnapshot 'E'
    $driveF = Get-DriveSnapshot 'F'

    $visualProcessedRoot = Join-Path $LegacyRawRoot 'visual_processed'
    $legacyRawStats = Get-PathStats $LegacyRawRoot
    $visualProcessedStats = Get-PathStats $visualProcessedRoot
    $rawTargetStats = Get-PathStats $RawTargetRoot
    $legacyEHotStats = Get-PathStats $LegacyEHotRoot
    $legacyELogsStats = Get-PathStats $LegacyEHotLogsRoot
    $dockerColdBackupStats = Get-PathStats $DockerColdBackupRoot
    $dSpikeStats = Get-PathStats 'D:\MarkOrbitData\spike'
    $dRuntimeStats = Get-PathStats 'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse-Spike'
    $eSpikeStats = Get-PathStats 'E:\MarkOrbitData\spike'
    $eToolingStats = Get-PathStats 'E:\MarkOrbitData\wsl-tooling\Ubuntu-24.04'
    $fRecoveryStats = Get-PathStats 'F:\MarkOrbitData\recovery'

    foreach ($entry in @($legacyRawStats,$visualProcessedStats,$rawTargetStats,$legacyEHotStats,$legacyELogsStats,$dockerColdBackupStats,$dSpikeStats,$dRuntimeStats,$eSpikeStats,$eToolingStats,$fRecoveryStats)) {
        if (-not [bool]$entry.complete) { throw "Candidate path inventory incomplete: $($entry.path): $($entry.enumeration_error)" }
    }

    $legacyEHotRefs = Get-PathReferences $LegacyEHotRoot $containerMounts $composeBinds
    $legacyELogsRefs = Get-PathReferences $LegacyEHotLogsRoot $containerMounts $composeBinds
    $legacyRawRefs = Get-PathReferences $LegacyRawRoot $containerMounts $composeBinds
    $visualProcessedRefs = Get-PathReferences $visualProcessedRoot $containerMounts $composeBinds

    $rawMetadataParityExact = $false
    if ([bool]$legacyRawStats.exists -and [bool]$rawTargetStats.exists) {
        $rawMetadataParityExact = Compare-TreeMetadataExact $LegacyRawRoot $RawTargetRoot
    }
    Write-Host "legacy_raw_to_f_metadata_parity_exact=$rawMetadataParityExact"

    $unexpectedLegacyRawComposeRefs = @($legacyRawRefs.compose_references | Where-Object {
        -not (Test-PathContains $visualProcessedRoot $_.normalized_source)
    })
    $unexpectedLegacyRawContainerRefs = @($legacyRawRefs.all_container_references | Where-Object {
        -not (Test-PathContains $visualProcessedRoot $_.normalized_source)
    })
    $protectedVisualProcessedBytes = if ([bool]$visualProcessedStats.exists) { [int64]$visualProcessedStats.total_bytes } else { [int64]0 }
    $legacyRawPotentialBytes = [int64][math]::Max([int64]0, [int64]([int64]$legacyRawStats.total_bytes - $protectedVisualProcessedBytes))
    $legacyRawPreferredCandidate = [bool](
        [bool]$legacyRawStats.exists -and
        $rawMetadataParityExact -and
        $unexpectedLegacyRawComposeRefs.Count -eq 0 -and
        $unexpectedLegacyRawContainerRefs.Count -eq 0
    )

    $legacyEHotPreferredCandidate = [bool](
        [bool]$legacyEHotStats.exists -and
        [int64]$legacyEHotStats.total_bytes -gt 0 -and
        [int64]$legacyEHotRefs.all_container_reference_count -eq 0 -and
        [int64]$legacyEHotRefs.compose_reference_count -eq 0
    )
    $legacyELogsPreferredCandidate = [bool](
        [bool]$legacyELogsStats.exists -and
        [int64]$legacyELogsStats.total_bytes -gt 0 -and
        [int64]$legacyELogsRefs.all_container_reference_count -eq 0 -and
        [int64]$legacyELogsRefs.compose_reference_count -eq 0
    )

    $preferredDReclaimable = if ($legacyRawPreferredCandidate) { $legacyRawPotentialBytes } else { [int64]0 }
    $preferredEReclaimable = [int64]0
    if ($legacyEHotPreferredCandidate) { $preferredEReclaimable += [int64]$legacyEHotStats.total_bytes }
    if ($legacyELogsPreferredCandidate) { $preferredEReclaimable += [int64]$legacyELogsStats.total_bytes }

    Write-Host 'rebalance_stage=deficit_and_coverage_math'
    $hostRecommendedPercent = [double]$sizing.reserve_policy.host_recommended_free_percent
    $hostHardPercent = [double]$sizing.reserve_policy.host_hard_free_percent
    $dRecommendedReserve = Get-ReserveBytes ([int64]$driveD.total_bytes) $hostRecommendedPercent
    $dHardReserve = Get-ReserveBytes ([int64]$driveD.total_bytes) $hostHardPercent
    $eRecommendedReserve = Get-ReserveBytes ([int64]$driveE.total_bytes) $hostRecommendedPercent
    $eHardReserve = Get-ReserveBytes ([int64]$driveE.total_bytes) $hostHardPercent
    $sourceCoexistenceBytes = [int64]$sizing.fit.coexistence_lower_bound_bytes
    $warmRecommendedBytes = [int64]$sizing.target_quotas.recommended.warm_candidate_capacity_bytes
    $warmHardBytes = [int64]$sizing.target_quotas.hard_floor.warm_candidate_capacity_bytes

    $dAdditionalFreeRecommended = Get-AdditionalFreeRequired ([int64]$driveD.free_bytes) $dRecommendedReserve $sourceCoexistenceBytes
    $dAdditionalFreeHard = Get-AdditionalFreeRequired ([int64]$driveD.free_bytes) $dHardReserve $sourceCoexistenceBytes
    $eAdditionalFreeRecommended = Get-AdditionalFreeRequired ([int64]$driveE.free_bytes) $eRecommendedReserve $warmRecommendedBytes
    $eAdditionalFreeHard = Get-AdditionalFreeRequired ([int64]$driveE.free_bytes) $eHardReserve $warmHardBytes

    $dRecommendedCoveredByPreferred = [bool]($preferredDReclaimable -ge $dAdditionalFreeRecommended)
    $dHardCoveredByPreferred = [bool]($preferredDReclaimable -ge $dAdditionalFreeHard)
    $eRecommendedCoveredByPreferred = [bool]($preferredEReclaimable -ge $eAdditionalFreeRecommended)
    $eHardCoveredByPreferred = [bool]($preferredEReclaimable -ge $eAdditionalFreeHard)

    $candidateState = if ($dRecommendedCoveredByPreferred -and $eRecommendedCoveredByPreferred) {
        'REBALANCE_RECOMMENDED_FLOOR_CANDIDATES_FOUND'
    } elseif ($dHardCoveredByPreferred -and $eRecommendedCoveredByPreferred) {
        'REBALANCE_TEMPORARY_HARD_FLOOR_CANDIDATES_FOUND'
    } elseif ($dHardCoveredByPreferred -and $eHardCoveredByPreferred) {
        'REBALANCE_HARD_FLOOR_CANDIDATES_FOUND'
    } else {
        'REBALANCE_CANDIDATE_EVIDENCE_INSUFFICIENT'
    }

    $nextGate = switch ($candidateState) {
        'REBALANCE_RECOMMENDED_FLOOR_CANDIDATES_FOUND' { 'PRODUCTION_REBALANCE_APPLY_PLAN' }
        'REBALANCE_TEMPORARY_HARD_FLOOR_CANDIDATES_FOUND' { 'PRODUCTION_REBALANCE_APPLY_PLAN_WITH_TEMPORARY_20_PERCENT_REVIEW' }
        'REBALANCE_HARD_FLOOR_CANDIDATES_FOUND' { 'PRODUCTION_REBALANCE_APPLY_PLAN_WITH_TEMPORARY_20_PERCENT_REVIEW' }
        default { 'PRODUCTION_REBALANCE_ADDITIONAL_INVENTORY' }
    }

    $productionAfter = Get-ProductionClickHouseHealth
    Assert-NoWorkerContainers
    if (-not $productionAfter['ready']) { throw 'Production ClickHouse must remain healthy after rebalance inventory.' }
    $envHashAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    $envUnchanged = [bool]($envHashBefore -eq $envHashAfter)
    if (-not $envUnchanged) { throw '.env changed during read-only rebalance inventory.' }

    $receipt = [ordered]@{
        receipt_version='PRODUCTION_STORAGE_REBALANCE_CANDIDATE_INVENTORY_V1'
        decision=$candidateState
        next_gate=$nextGate
        read_only=$true
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        sizing=[ordered]@{
            report_path=$sizingResult['path']
            decision=[string]$sizing.decision
            next_gate=[string]$sizing.next_gate
            final_capacity_state=[string]$sizing.fit.final_capacity_state
            coexistence_state=[string]$sizing.fit.coexistence_state
        }
        production=[ordered]@{
            clickhouse_ready_before=[bool]$productionBefore['ready']
            clickhouse_ready_after=[bool]$productionAfter['ready']
            accepted_volume=$AcceptedVolume
            accepted_production_mount_ready=$acceptedProductionMountReady
            worker_container_count=0
        }
        drives=[ordered]@{ D=$driveD; E=$driveE; F=$driveF }
        deficits=[ordered]@{
            d_additional_free_recommended_bytes=$dAdditionalFreeRecommended
            d_additional_free_hard_bytes=$dAdditionalFreeHard
            e_additional_free_recommended_bytes=$eAdditionalFreeRecommended
            e_additional_free_hard_bytes=$eAdditionalFreeHard
            source_coexistence_lower_bound_bytes=$sourceCoexistenceBytes
            warm_recommended_capacity_bytes=$warmRecommendedBytes
            warm_hard_capacity_bytes=$warmHardBytes
        }
        preferred_candidates=[ordered]@{
            D=[ordered]@{
                legacy_raw=[ordered]@{
                    stats=$legacyRawStats
                    protected_visual_processed_stats=$visualProcessedStats
                    f_target_stats=$rawTargetStats
                    metadata_parity_exact=$rawMetadataParityExact
                    unexpected_compose_reference_count=$unexpectedLegacyRawComposeRefs.Count
                    unexpected_container_reference_count=$unexpectedLegacyRawContainerRefs.Count
                    preferred_candidate=$legacyRawPreferredCandidate
                    potential_reclaimable_bytes=$legacyRawPotentialBytes
                    delete_authorized=$false
                }
                total_preferred_reclaimable_bytes=$preferredDReclaimable
                recommended_deficit_covered=$dRecommendedCoveredByPreferred
                hard_deficit_covered=$dHardCoveredByPreferred
            }
            E=[ordered]@{
                legacy_ntfs_clickhouse=[ordered]@{
                    stats=$legacyEHotStats
                    references=$legacyEHotRefs
                    preferred_candidate=$legacyEHotPreferredCandidate
                    delete_authorized=$false
                }
                legacy_ntfs_clickhouse_logs=[ordered]@{
                    stats=$legacyELogsStats
                    references=$legacyELogsRefs
                    preferred_candidate=$legacyELogsPreferredCandidate
                    delete_authorized=$false
                }
                total_preferred_reclaimable_bytes=$preferredEReclaimable
                recommended_deficit_covered=$eRecommendedCoveredByPreferred
                hard_deficit_covered=$eHardCoveredByPreferred
            }
        }
        retained_or_secondary=[ordered]@{
            e_docker_cold_backup=[ordered]@{
                stats=$dockerColdBackupStats
                role='COLD_RECOVERY_BACKUP_SECONDARY_CANDIDATE_ONLY'
                direct_delete_authorized=$false
                direct_move_authorized=$false
                f_relocation_requires_copy_hash_and_separate_source_delete_gate=$true
            }
            d_spike=[ordered]@{ stats=$dSpikeStats; role='RETAINED_ARCHITECTURE_PROOF'; reclaim_counted=$false }
            d_runtime=[ordered]@{ stats=$dRuntimeStats; role='RETAINED_WSL_RUNTIME_PROOF'; reclaim_counted=$false }
            e_spike=[ordered]@{ stats=$eSpikeStats; role='RETAINED_ARCHITECTURE_PROOF'; reclaim_counted=$false }
            e_tooling=[ordered]@{ stats=$eToolingStats; role='RETAINED_WSL_TOOLING'; reclaim_counted=$false }
            f_recovery=[ordered]@{ stats=$fRecoveryStats; role='RETAINED_RECOVERY'; delete_authorized=$false }
        }
        reference_evidence=[ordered]@{
            container_mounts=@($containerMounts)
            compose_bind_mounts=@($composeBinds)
            legacy_raw_references=$legacyRawRefs
            visual_processed_references=$visualProcessedRefs
        }
        constraints=[ordered]@{
            candidate_inventory_only=$true
            temporary_20_percent_floor_apply_authorized=$false
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
            source_copy_authorized=$false
            corpus_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
        production_invariant_preserved=[bool]($productionBefore['ready'] -and $productionAfter['ready'])
        env_unchanged=$envUnchanged
        file_delete_performed=$false
        file_move_performed=$false
        file_copy_performed=$false
        vhdx_create_performed=$false
        vhdx_resize_performed=$false
        vhdx_mount_performed=$false
        vhdx_move_performed=$false
        wsl_unmount_performed=$false
        wsl_shutdown_performed=$false
        docker_restart_performed=$false
        docker_prune_performed=$false
        production_clickhouse_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        source_copy_performed=$false
        corpus_replay_performed=$false
    }

    $receiptPath = Join-Path $evidenceDir 'production_storage_rebalance_candidate_inventory.json'
    $receipt | ConvertTo-Json -Depth 28 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    $gib = [math]::Pow(1024,3)
    Write-Host '===== PRODUCTION STORAGE REBALANCE CANDIDATE RESULT ====='
    Write-Host "decision=$candidateState"
    Write-Host "next_gate=$nextGate"
    Write-Host ("d_additional_free_recommended_gib={0:N2}" -f ($dAdditionalFreeRecommended / $gib))
    Write-Host ("d_additional_free_hard_gib={0:N2}" -f ($dAdditionalFreeHard / $gib))
    Write-Host ("e_additional_free_recommended_gib={0:N2}" -f ($eAdditionalFreeRecommended / $gib))
    Write-Host ("e_additional_free_hard_gib={0:N2}" -f ($eAdditionalFreeHard / $gib))
    Write-Host "legacy_e_hot_unreferenced=$legacyEHotPreferredCandidate"
    Write-Host ("legacy_e_hot_gib={0:N2}" -f ([int64]$legacyEHotStats.total_bytes / $gib))
    Write-Host ("legacy_e_logs_gib={0:N2}" -f ([int64]$legacyELogsStats.total_bytes / $gib))
    Write-Host ("preferred_e_reclaimable_gib={0:N2}" -f ($preferredEReclaimable / $gib))
    Write-Host "legacy_raw_to_f_metadata_parity_exact=$rawMetadataParityExact"
    Write-Host ("legacy_raw_total_gib={0:N2}" -f ([int64]$legacyRawStats.total_bytes / $gib))
    Write-Host ("visual_processed_protected_gib={0:N2}" -f ($protectedVisualProcessedBytes / $gib))
    Write-Host ("preferred_d_reclaimable_gib={0:N2}" -f ($preferredDReclaimable / $gib))
    Write-Host "d_recommended_deficit_covered=$dRecommendedCoveredByPreferred"
    Write-Host "d_hard_deficit_covered=$dHardCoveredByPreferred"
    Write-Host "e_recommended_deficit_covered=$eRecommendedCoveredByPreferred"
    Write-Host "e_hard_deficit_covered=$eHardCoveredByPreferred"
    Write-Host ("e_docker_cold_backup_gib={0:N2}" -f ([int64]$dockerColdBackupStats.total_bytes / $gib))
    Write-Host ("f_recovery_gib={0:N2}" -f ([int64]$fRecoveryStats.total_bytes / $gib))
    Write-Host 'candidate_inventory_only=True'
    Write-Host 'temporary_20_percent_floor_apply_authorized=False'
    Write-Host 'legacy_e_hot_delete_authorized=False'
    Write-Host 'legacy_raw_delete_authorized=False'
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'
    Write-Host "production_invariant_preserved=$([bool]$receipt.production_invariant_preserved)"
    Write-Host "env_unchanged=$envUnchanged"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_STORAGE_REBALANCE_CANDIDATE_INVENTORY_DONE'

    Assert-ExactMain 'exit'
}
finally { Pop-Location }
