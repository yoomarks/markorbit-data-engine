[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$TargetRawRoot = 'F:\MarkOrbitData\raw',
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
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $lines = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code ${exitCode}: $($lines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
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

function Get-ConfiguredRawPath {
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        throw '.env is required for Raw forward-copy preflight.'
    }
    $line = Get-Content -LiteralPath $envPath -Encoding UTF8 |
        Where-Object { $_ -match '^\s*RAW_DATA_PATH\s*=' } |
        Select-Object -First 1
    if (-not $line) {
        throw 'RAW_DATA_PATH is not configured in .env.'
    }
    $value = (($line -split '=',2)[1]).Trim().Trim('"').Trim("'")
    if (-not [System.IO.Path]::IsPathRooted($value)) {
        $value = Join-Path $repoRoot $value
    }
    if (-not (Test-Path -LiteralPath $value -PathType Container)) {
        throw "Configured RAW_DATA_PATH does not exist: $value"
    }
    return (Resolve-Path -LiteralPath $value).Path
}

function Get-TreeStats([string]$Root) {
    $count = [int64]0
    $bytes = [int64]0
    foreach ($path in [System.IO.Directory]::EnumerateFiles($Root, '*', [System.IO.SearchOption]::AllDirectories)) {
        $item = New-Object System.IO.FileInfo($path)
        $count += 1
        $bytes += [int64]$item.Length
    }
    return [ordered]@{ file_count=$count; total_bytes=$bytes }
}

function Get-WorkerContainerCount {
    $probe = Invoke-NativeText 'docker' @('compose','ps','-a','-q','worker') -AllowFailure
    if ($probe['exit_code'] -ne 0) { return -1 }
    return @($probe['lines'] | Where-Object { $_.Trim() }).Count
}

function Get-ProductionClickHouseHealth {
    $containerProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($containerProbe['lines']) -join '').Trim()
    if (-not $containerId) {
        return [ordered]@{ ready=$false; health=$null }
    }
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim()
    $ready = [bool](
        $healthProbe['exit_code'] -eq 0 -and
        $health -eq 'healthy' -and
        $sqlProbe['exit_code'] -eq 0 -and
        ((@($sqlProbe['lines']) -join '').Trim() -eq '1')
    )
    return [ordered]@{ ready=$ready; health=$health }
}

try {
    Write-Host '===== RAW FORWARD-COPY TO F PREFLIGHT ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'Raw forward-copy preflight must run from local main.'
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Raw forward-copy preflight requires elevated Administrator PowerShell.'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "raw_forward_copy_preflight_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

    Write-Host 'preflight_stage=source_snapshot'
    $sourceRoot = Get-ConfiguredRawPath
    $sourceStats = Get-TreeStats $sourceRoot
    $sourceDriveRoot = [System.IO.Path]::GetPathRoot($sourceRoot)
    Write-Host "source_root=$sourceRoot"
    Write-Host "source_drive_root=$sourceDriveRoot"
    Write-Host "source_file_count=$($sourceStats['file_count'])"
    Write-Host "source_total_bytes=$($sourceStats['total_bytes'])"

    Write-Host 'preflight_stage=production_invariants'
    $workerCount = Get-WorkerContainerCount
    $production = Get-ProductionClickHouseHealth
    Write-Host "worker_container_count=$workerCount"
    Write-Host "production_clickhouse_ready=$($production['ready'])"
    Write-Host "production_clickhouse_health=$($production['health'])"

    Write-Host 'preflight_stage=target_snapshot'
    $targetFullPath = [System.IO.Path]::GetFullPath($TargetRawRoot)
    $targetDriveRoot = [System.IO.Path]::GetPathRoot($targetFullPath)
    $targetExists = Test-Path -LiteralPath $targetFullPath -PathType Container
    $targetStats = if ($targetExists) { Get-TreeStats $targetFullPath } else { [ordered]@{ file_count=[int64]0; total_bytes=[int64]0 } }
    $targetState = if (-not $targetExists) { 'ABSENT' } elseif ([int64]$targetStats['file_count'] -eq 0) { 'EMPTY' } else { 'NONEMPTY' }
    Write-Host "target_root=$targetFullPath"
    Write-Host "target_drive_root=$targetDriveRoot"
    Write-Host "target_state=$targetState"
    Write-Host "target_file_count=$($targetStats['file_count'])"
    Write-Host "target_total_bytes=$($targetStats['total_bytes'])"

    $fVolume = Get-Volume -DriveLetter F -ErrorAction Stop
    $fTotalBytes = [int64]$fVolume.Size
    $fFreeBytes = [int64]$fVolume.SizeRemaining
    $twentyPercentReserve = [int64][Math]::Ceiling([double]$fTotalBytes * 0.20)
    $minimumReserve = [int64](512GB)
    $reserveFloorBytes = [int64][Math]::Max($twentyPercentReserve, $minimumReserve)
    $projectedFreeAfterCopy = [int64]($fFreeBytes - [int64]$sourceStats['total_bytes'])
    Write-Host "target_filesystem=$($fVolume.FileSystem)"
    Write-Host "target_drive_total_bytes=$fTotalBytes"
    Write-Host "target_drive_free_bytes=$fFreeBytes"
    Write-Host "reserve_floor_bytes=$reserveFloorBytes"
    Write-Host "projected_free_after_copy_bytes=$projectedFreeAfterCopy"

    $blockers = @()
    if ($sourceDriveRoot -ne 'D:\') { $blockers += 'SOURCE_RAW_NOT_ON_D' }
    if ([int64]$sourceStats['file_count'] -le 0 -or [int64]$sourceStats['total_bytes'] -le 0) { $blockers += 'SOURCE_RAW_EMPTY' }
    if ($targetDriveRoot -ne 'F:\') { $blockers += 'TARGET_RAW_NOT_ON_F' }
    if ([string]$fVolume.FileSystem -ne 'NTFS') { $blockers += 'TARGET_F_FILESYSTEM_NOT_NTFS' }
    if ($targetState -eq 'NONEMPTY') { $blockers += 'TARGET_RAW_ROOT_NONEMPTY' }
    if ($workerCount -ne 0) { $blockers += 'WORKER_CONTAINER_COUNT_NOT_ZERO' }
    if (-not [bool]$production['ready']) { $blockers += 'PRODUCTION_CLICKHOUSE_NOT_HEALTHY' }
    if ($projectedFreeAfterCopy -lt $reserveFloorBytes) { $blockers += 'F_HEADROOM_BELOW_RESERVE_AFTER_COPY' }

    $ready = ($blockers.Count -eq 0)
    $decision = if ($ready) { 'RAW_FORWARD_COPY_PREFLIGHT_READY' } else { 'RAW_FORWARD_COPY_PREFLIGHT_BLOCKED' }

    $receipt = [ordered]@{
        schema='RAW_FORWARD_COPY_PREFLIGHT_V1'
        generated_at_utc=(Get-Date).ToUniversalTime().ToString('o')
        decision=$decision
        ready_for_forward_copy_operator=$ready
        source_root=$sourceRoot
        source_drive_root=$sourceDriveRoot
        source_file_count=[int64]$sourceStats['file_count']
        source_total_bytes=[int64]$sourceStats['total_bytes']
        target_root=$targetFullPath
        target_drive_root=$targetDriveRoot
        target_state=$targetState
        target_file_count=[int64]$targetStats['file_count']
        target_total_bytes=[int64]$targetStats['total_bytes']
        target_filesystem=[string]$fVolume.FileSystem
        target_drive_total_bytes=$fTotalBytes
        target_drive_free_bytes=$fFreeBytes
        reserve_floor_bytes=$reserveFloorBytes
        projected_free_after_copy_bytes=$projectedFreeAfterCopy
        worker_container_count=$workerCount
        production_clickhouse_ready=[bool]$production['ready']
        blockers=@($blockers)
        copy_sequence=@('FORWARD_COPY_PRESERVE_D_SOURCE','BYTE_PARITY','RUNTIME_BIND_CUTOVER','POST_CUTOVER_ACCEPTANCE','D_SOURCE_RETENTION_REVIEW')
        copy_authorized=$false
        env_change_authorized=$false
        raw_move_authorized=$false
        raw_delete_authorized=$false
        vhdx_mutation_performed=$false
        wsl_mutation_performed=$false
        docker_restart_performed=$false
        clickhouse_mutation_performed=$false
        corpus_replay_performed=$false
        us_package_2_authorized=$false
        us_bulk_authorized=$false
    }

    $reportPath = Join-Path $evidenceDir 'raw_forward_copy_preflight.json'
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host '===== RAW FORWARD-COPY TO F PREFLIGHT RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "ready_for_forward_copy_operator=$ready"
    Write-Host "copy_authorized=False"
    Write-Host "env_change_authorized=False"
    Write-Host "raw_delete_authorized=False"
    Write-Host "blocker_count=$($blockers.Count)"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'RAW_FORWARD_COPY_PREFLIGHT_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
