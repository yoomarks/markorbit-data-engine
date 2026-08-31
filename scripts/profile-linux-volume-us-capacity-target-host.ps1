[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts = 91,
    [string]$AcceptedVolume = "markorbit-data-engine_clickhouse_data",
    [ValidateRange(1, 99)]
    [double]$HotFloorPercent = 30,
    [string]$PilotReceiptPath = "",
    [string]$EvidenceRoot = "reports"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $rendered = @($output | ForEach-Object { $_.ToString() })
    if ($exitCode -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code $exitCode`: $($rendered -join [Environment]::NewLine)"
    }
    return $rendered
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift detected during $Phase."
    }
    if (git status --porcelain) {
        throw "Working tree must be clean during $Phase."
    }
}

function Get-SingleRunningClickHouseId {
    $ids = @(docker compose ps --status running -q clickhouse |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
        throw "Exactly one running ClickHouse container is required."
    }
    return $ids[0]
}

function Assert-NoWorkerContainers {
    $ids = @(docker compose ps -a -q worker |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect worker containers." }
    Write-Host "worker_container_count_all_states=$($ids.Count)"
    if ($ids.Count -ne 0) {
        throw "Persistent/disposable worker containers must be absent at the capacity profile boundary."
    }
}

function Read-JsonFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label file missing: $Path"
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "$Label JSON invalid: $($_.Exception.Message)"
    }
}

function Invoke-WorkerJson([string[]]$PythonArgs, [string]$Label) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $lines = @(docker compose run --rm --no-deps -T `
            --volume "${repoRoot}\app:/app/app:ro" `
            worker python @PythonArgs)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $json = ($lines | ForEach-Object { $_.ToString() }) -join "`n"
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode."
    }
    if (-not $json.Trim()) { throw "$Label produced no JSON." }
    try { return $json | ConvertFrom-Json }
    catch { throw "$Label produced invalid JSON: $($_.Exception.Message)" }
}

function Get-LatestAcceptedPilotReceipt {
    if ($PilotReceiptPath) {
        return (Resolve-Path -LiteralPath $PilotReceiptPath).Path
    }
    $candidates = @(Get-ChildItem -LiteralPath $EvidenceRoot -Recurse -Filter 'pilot_receipt.json' -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending)
    foreach ($candidate in $candidates) {
        try {
            $receipt = Get-Content -LiteralPath $candidate.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($receipt.receipt_version -eq 'US_BOUNDED_CAPACITY_PILOT_RECEIPT_V1' -and
                [bool]$receipt.safe -and
                [bool]$receipt.projection_input_ready -and
                $receipt.status -eq 'PASS') {
                return $candidate.FullName
            }
        }
        catch {}
    }
    throw "No accepted US_BOUNDED_CAPACITY_PILOT_RECEIPT_V1 found under $EvidenceRoot."
}

function Get-DriveSnapshot([string]$Path) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $root = [System.IO.Path]::GetPathRoot($resolved)
    if (-not $root) { throw "Unable to determine drive root for $resolved" }
    $drive = [System.IO.DriveInfo]::new($root)
    return [ordered]@{
        path = $resolved
        drive_root = $root
        free_bytes = [int64]$drive.AvailableFreeSpace
        total_bytes = [int64]$drive.TotalSize
    }
}

function Get-RawDataHostPath {
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        throw ".env is required to resolve RAW_DATA_PATH."
    }
    $line = Get-Content -LiteralPath $envPath -Encoding UTF8 |
        Where-Object { $_ -match '^\s*RAW_DATA_PATH\s*=' } |
        Select-Object -First 1
    if (-not $line) { throw "RAW_DATA_PATH is missing from .env." }
    $value = (($line -split '=', 2)[1]).Trim().Trim('"').Trim("'")
    if (-not [System.IO.Path]::IsPathRooted($value)) { $value = Join-Path $repoRoot $value }
    $probe = $value
    while ($probe -and -not (Test-Path -LiteralPath $probe)) {
        $parent = Split-Path -Parent $probe
        if (-not $parent -or $parent -eq $probe) { break }
        $probe = $parent
    }
    if (-not $probe -or -not (Test-Path -LiteralPath $probe)) {
        throw "Unable to resolve RAW_DATA_PATH host drive from $value"
    }
    return $probe
}

function Add-VhdxCandidates([System.Collections.ArrayList]$List, [string]$Root) {
    if (-not $Root -or -not (Test-Path -LiteralPath $Root -PathType Container)) { return }
    foreach ($item in @(Get-ChildItem -LiteralPath $Root -Recurse -Filter '*.vhdx' -File -ErrorAction SilentlyContinue)) {
        if (-not $List.Contains($item.FullName)) { [void]$List.Add($item.FullName) }
    }
}

function Get-DockerVhdxEvidence {
    $paths = New-Object System.Collections.ArrayList
    Add-VhdxCandidates $paths 'D:\DockerData\DockerDesktopWSL'
    Add-VhdxCandidates $paths (Join-Path $env:LOCALAPPDATA 'Docker\wsl')

    $entries = @()
    $driveRoots = @()
    foreach ($path in @($paths)) {
        $item = Get-Item -LiteralPath $path
        $drive = Get-DriveSnapshot $item.FullName
        $entries += [ordered]@{
            path = $item.FullName
            file_bytes = [int64]$item.Length
            drive_root = $drive.drive_root
            drive_free_bytes = $drive.free_bytes
            drive_total_bytes = $drive.total_bytes
        }
        if ($driveRoots -notcontains $drive.drive_root) { $driveRoots += $drive.drive_root }
    }
    return [ordered]@{
        candidate_count = $entries.Count
        candidates = @($entries)
        backing_drive_roots = @($driveRoots)
        backing_drive_unambiguous = [bool]($driveRoots.Count -eq 1)
    }
}

try {
    Write-Host '===== LINUX-VOLUME US CAPACITY PROFILE ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw "Capacity profile must run from local main."
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Unable to fetch origin/main." }
    Assert-ExactMain 'entry'
    Assert-NoWorkerContainers

    $clickhouseId = Get-SingleRunningClickHouseId
    $health = (docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $clickhouseId).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $health -ne 'healthy') {
        throw "ClickHouse must be healthy. observed=$health"
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "linux_volume_us_capacity_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    Write-Host 'capacity_stage=linux_volume_contract'
    $storageContractPath = Join-Path $evidenceDir 'storage_contract.json'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot 'assert-clickhouse-active-hot-storage-contract.ps1') `
        -OutputPath $storageContractPath
    if ($LASTEXITCODE -ne 0) { throw "Linux-volume storage contract failed." }
    $storageContract = Read-JsonFile $storageContractPath 'storage contract'
    if ([string]$storageContract.actual_mount_type -ne 'volume' -or
        [string]$storageContract.actual_mount_name -ne $AcceptedVolume -or
        @($storageContract.blockers).Count -ne 0 -or
        -not [bool]$storageContract.safe_for_clickhouse_merge_tree_writes) {
        throw "Accepted Linux named-volume contract is not active."
    }

    Write-Host 'capacity_stage=clickhouse_filesystem'
    $headroom = Invoke-WorkerJson @(
        '-m','app.storage_headroom',
        '--minimum-free-gib','0',
        '--minimum-free-percent','0',
        '--reserve-gib','0',
        '--compact'
    ) 'ClickHouse disk profile'
    if (-not $headroom.disk -or [string]$headroom.disk.name -ne 'default') {
        throw "ClickHouse default disk state is missing."
    }
    $hotTotal = [int64]$headroom.disk.total_space
    $hotFree = [int64]$headroom.disk.free_space
    if ($hotTotal -le 0 -or $hotFree -lt 0 -or $hotFree -gt $hotTotal) {
        throw "ClickHouse default disk capacity is invalid."
    }

    $storageProfilePath = Join-Path $evidenceDir 'active_storage.json'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot 'profile-storage-capacity.ps1') `
        -Compact -OutputPath $storageProfilePath
    if ($LASTEXITCODE -ne 0) { throw "Active storage profile failed." }
    $activeStorage = Read-JsonFile $storageProfilePath 'active storage profile'

    Write-Host 'capacity_stage=remaining_us_source_inventory'
    $remaining = Invoke-WorkerJson @(
        '-m','app.us.remaining_capacity_inventory',
        '--expected-history-parts',"$ExpectedHistoryParts",
        '--compact'
    ) 'Remaining US source inventory'
    if (-not [bool]$remaining.safe -or $remaining.status -ne 'PASS') {
        throw "Remaining US source inventory is not authoritative/safe."
    }

    $pilotPath = Get-LatestAcceptedPilotReceipt
    $pilot = Read-JsonFile $pilotPath 'pilot receipt'
    if ($pilot.receipt_version -ne 'US_BOUNDED_CAPACITY_PILOT_RECEIPT_V1' -or
        -not [bool]$pilot.safe -or
        -not [bool]$pilot.projection_input_ready -or
        $pilot.status -ne 'PASS') {
        throw "Pilot receipt is not accepted for capacity projection input."
    }
    $pilotRaw = [int64]$pilot.pilot.raw_bytes
    $pilotHot = [int64]$pilot.pilot.hot_bytes
    if ($pilotRaw -le 0 -or $pilotHot -le 0) { throw "Pilot receipt has invalid raw/hot bytes." }

    Write-Host 'capacity_stage=docker_volume_and_host_backing'
    $volumeJson = (Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume)) -join "`n"
    $volumeInspect = @($volumeJson | ConvertFrom-Json)
    if ($volumeInspect.Count -ne 1) { throw "Expected exactly one Docker volume inspect result." }

    $dockerSystemDfPath = Join-Path $evidenceDir 'docker-system-df.txt'
    (Invoke-NativeText 'docker' @('system','df')) | Set-Content -LiteralPath $dockerSystemDfPath -Encoding UTF8
    $dockerVhdx = Get-DockerVhdxEvidence
    $rawHost = Get-DriveSnapshot (Get-RawDataHostPath)

    $floorFraction = $HotFloorPercent / 100.0
    $hotFloorBytes = [int64][math]::Ceiling($hotTotal * $floorFraction)
    $currentFloorDeficit = [int64][math]::Max(0, $hotFloorBytes - $hotFree)
    $currentHotBudget = [int64][math]::Max(0, $hotFree - $hotFloorBytes)
    $remainingRaw = [int64]$remaining.remaining_raw_bytes
    $projectedRemainingHot = [int64][math]::Ceiling($remainingRaw * ([double]$pilotHot / [double]$pilotRaw))
    $usedBytes = [int64]($hotTotal - $hotFree)
    $requiredTotal = [int64][math]::Ceiling(($usedBytes + $projectedRemainingHot) / (1.0 - $floorFraction))
    $minimumAdditionalTotal = [int64][math]::Max(0, $requiredTotal - $hotTotal)
    $currentCapacityCanPreserveFloor = [bool]($projectedRemainingHot -le $currentHotBudget)

    $blockers = @()
    if (-not [bool]$dockerVhdx.backing_drive_unambiguous) {
        $blockers += 'DOCKER_DESKTOP_VHDX_BACKING_DRIVE_AMBIGUOUS'
    }
    if ($dockerVhdx.candidate_count -eq 0) {
        $blockers += 'DOCKER_DESKTOP_VHDX_NOT_DISCOVERED'
    }
    if (-not $currentCapacityCanPreserveFloor) {
        $blockers += 'CURRENT_LINUX_FILESYSTEM_CANNOT_PRESERVE_30_PERCENT_FLOOR_FOR_REMAINING_US'
    }

    $report = [ordered]@{
        profile_version = 'LINUX_VOLUME_US_CAPACITY_PROFILE_V1'
        read_only = $true
        status = if ($blockers.Count -eq 0) { 'READY_FOR_FORMAL_PROJECTION' } else { 'NO_GO_CURRENT_CAPACITY' }
        full_corpus_import_authorized = $false
        engine_sha = $ExpectedMainSha.Trim().ToLowerInvariant()
        accepted_volume = [ordered]@{
            name = $AcceptedVolume
            driver = [string]$volumeInspect[0].Driver
            mountpoint = [string]$volumeInspect[0].Mountpoint
            actual_mount_type = [string]$storageContract.actual_mount_type
            active_rw = [bool]$storageContract.mount_rw
        }
        clickhouse = [ordered]@{
            total_bytes = $hotTotal
            free_bytes = $hotFree
            used_bytes = $usedBytes
            free_percent = [double]$headroom.disk.free_percent
            active_table_bytes = [int64]$activeStorage.active_bytes
            active_rows = [int64]$activeStorage.active_rows
        }
        remaining_us = [ordered]@{
            package_count = [int]$remaining.remaining_count
            success_prefix_count = [int]$remaining.success_prefix_count
            compressed_raw_bytes = $remainingRaw
            next_step = $remaining.next_step
            raw_bytes_already_present_on_host_storage = [bool]$remaining.source_bytes_already_present_on_raw_storage
            incremental_raw_copy_bytes_required_by_replay = [int64]$remaining.incremental_raw_copy_bytes_required_by_replay
        }
        pilot = [ordered]@{
            receipt_path = $pilotPath
            receipt_identity = [string]$pilot.pilot.receipt_identity
            raw_bytes = $pilotRaw
            hot_bytes = $pilotHot
            measured_raw_to_hot_ratio = [double]$pilotHot / [double]$pilotRaw
            rows = [int64]$pilot.pilot.rows
        }
        hot_floor = [ordered]@{
            policy_percent = $HotFloorPercent
            floor_bytes_at_current_total = $hotFloorBytes
            current_floor_deficit_bytes = $currentFloorDeficit
            current_incremental_hot_budget_bytes = $currentHotBudget
            projected_remaining_us_hot_bytes = $projectedRemainingHot
            current_capacity_can_preserve_floor_after_remaining_us = $currentCapacityCanPreserveFloor
            minimum_linux_filesystem_total_bytes_for_remaining_us = $requiredTotal
            minimum_additional_linux_filesystem_total_bytes = $minimumAdditionalTotal
        }
        docker_desktop = $dockerVhdx
        raw_cold_host = $rawHost
        docker_system_df_path = $dockerSystemDfPath
        blockers = @($blockers)
        destructive_action_performed = $false
        docker_prune_performed = $false
        volume_resize_performed = $false
        service_restart_performed = $false
        schema_apply_performed = $false
        corpus_replay_performed = $false
        next_action = if ($currentCapacityCanPreserveFloor) { 'RUN_FORMAL_US_FULL_CORPUS_PROJECTION' } else { 'EXPAND_OR_RECLAIM_DOCKER_LINUX_CAPACITY_WITH_SEPARATE_ACCEPTED_PLAN' }
    }

    $reportPath = Join-Path $evidenceDir 'linux_volume_us_capacity_profile.json'
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    $gib = [math]::Pow(1024, 3)
    Write-Host '===== LINUX-VOLUME US CAPACITY RESULT ====='
    Write-Host ("clickhouse_total_gib={0:N2}" -f ($hotTotal / $gib))
    Write-Host ("clickhouse_free_gib={0:N2}" -f ($hotFree / $gib))
    Write-Host ("clickhouse_free_percent={0:N2}" -f $headroom.disk.free_percent)
    Write-Host "remaining_us_packages=$($remaining.remaining_count)"
    Write-Host ("remaining_us_raw_gib={0:N2}" -f ($remainingRaw / $gib))
    Write-Host ("pilot_raw_to_hot_ratio={0:N4}" -f ([double]$pilotHot / [double]$pilotRaw))
    Write-Host ("projected_remaining_us_hot_gib={0:N2}" -f ($projectedRemainingHot / $gib))
    Write-Host ("current_30pct_floor_deficit_gib={0:N2}" -f ($currentFloorDeficit / $gib))
    Write-Host ("minimum_additional_linux_fs_total_gib={0:N2}" -f ($minimumAdditionalTotal / $gib))
    Write-Host "docker_vhdx_candidate_count=$($dockerVhdx.candidate_count)"
    Write-Host "docker_vhdx_backing_drive_unambiguous=$($dockerVhdx.backing_drive_unambiguous)"
    Write-Host "full_corpus_import_authorized=False"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host "LINUX_VOLUME_US_CAPACITY_PROFILE_DONE"

    Assert-NoWorkerContainers
    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
