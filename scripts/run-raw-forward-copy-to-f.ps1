[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$TargetRawRoot = 'F:\MarkOrbitData\raw',
    [string]$EvidenceRoot = 'reports',
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
        throw '.env is required for Raw forward-copy.'
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

function Get-TreeManifest([string]$Root) {
    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
    $prefix = $resolvedRoot + '\'
    $items = @()
    foreach ($path in [System.IO.Directory]::EnumerateFiles($resolvedRoot, '*', [System.IO.SearchOption]::AllDirectories)) {
        $fullPath = [System.IO.Path]::GetFullPath($path)
        if (-not $fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "File escaped expected root: $fullPath"
        }
        $relativePath = $fullPath.Substring($prefix.Length)
        $info = New-Object System.IO.FileInfo($fullPath)
        $items += [pscustomobject]@{
            relative_path=$relativePath
            key=$relativePath.ToLowerInvariant()
            full_path=$fullPath
            length=[int64]$info.Length
        }
    }
    return @($items | Sort-Object relative_path)
}

function Get-ManifestStats([object[]]$Manifest) {
    $bytes = [int64]0
    foreach ($item in @($Manifest)) { $bytes += [int64]$item.length }
    return [ordered]@{ file_count=[int64]@($Manifest).Count; total_bytes=$bytes }
}

function Get-WorkerContainerCount {
    $probe = Invoke-NativeText 'docker' @('compose','ps','-a','-q','worker') -AllowFailure
    if ($probe['exit_code'] -ne 0) { return -1 }
    return @($probe['lines'] | Where-Object { $_.Trim() }).Count
}

function Get-ProductionClickHouseHealth {
    $containerProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($containerProbe['lines']) -join '').Trim()
    if (-not $containerId) { return [ordered]@{ ready=$false; health=$null } }
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

function Compare-TargetCompatibility([object[]]$SourceManifest, [string]$TargetRoot) {
    $sourceMap = @{}
    foreach ($item in @($SourceManifest)) { $sourceMap[$item.key] = $item }
    $foreign = @()
    $targetManifest = @()
    if (Test-Path -LiteralPath $TargetRoot -PathType Container) {
        $targetManifest = @(Get-TreeManifest $TargetRoot)
        foreach ($item in $targetManifest) {
            if (-not $sourceMap.ContainsKey($item.key)) { $foreign += $item.relative_path }
        }
    }
    return [ordered]@{
        target_manifest=@($targetManifest)
        foreign_files=@($foreign)
    }
}

function Compare-MetadataExact([object[]]$SourceManifest, [object[]]$TargetManifest) {
    $targetMap = @{}
    foreach ($item in @($TargetManifest)) { $targetMap[$item.key] = $item }
    $missing = @()
    $sizeMismatch = @()
    foreach ($source in @($SourceManifest)) {
        if (-not $targetMap.ContainsKey($source.key)) {
            $missing += $source.relative_path
            continue
        }
        $target = $targetMap[$source.key]
        if ([int64]$target.length -ne [int64]$source.length) {
            $sizeMismatch += $source.relative_path
        }
    }
    $sourceKeys = @{}
    foreach ($source in @($SourceManifest)) { $sourceKeys[$source.key] = $true }
    $extra = @($TargetManifest | Where-Object { -not $sourceKeys.ContainsKey($_.key) } | ForEach-Object { $_.relative_path })
    return [ordered]@{
        exact=($missing.Count -eq 0 -and $sizeMismatch.Count -eq 0 -and $extra.Count -eq 0 -and @($SourceManifest).Count -eq @($TargetManifest).Count)
        missing=@($missing)
        size_mismatch=@($sizeMismatch)
        extra=@($extra)
    }
}

function Test-SourceManifestStable([object[]]$Before, [object[]]$After) {
    $comparison = Compare-MetadataExact $Before $After
    return [bool]$comparison['exact']
}

try {
    Write-Host '===== RAW FORWARD-COPY TO F ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'Raw forward-copy must run from local main.'
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Raw forward-copy requires elevated Administrator PowerShell.'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "raw_forward_copy_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

    Write-Host 'copy_stage=source_snapshot'
    $sourceRoot = Get-ConfiguredRawPath
    $sourceDriveRoot = [System.IO.Path]::GetPathRoot($sourceRoot)
    $sourceManifestBefore = @(Get-TreeManifest $sourceRoot)
    $sourceStatsBefore = Get-ManifestStats $sourceManifestBefore
    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    Write-Host "source_root=$sourceRoot"
    Write-Host "source_drive_root=$sourceDriveRoot"
    Write-Host "source_file_count=$($sourceStatsBefore['file_count'])"
    Write-Host "source_total_bytes=$($sourceStatsBefore['total_bytes'])"

    Write-Host 'copy_stage=production_invariants_before'
    $workerBefore = Get-WorkerContainerCount
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "worker_container_count_before=$workerBefore"
    Write-Host "production_clickhouse_ready_before=$($productionBefore['ready'])"
    Write-Host "production_clickhouse_health_before=$($productionBefore['health'])"

    Write-Host 'copy_stage=target_gate'
    $targetFullPath = [System.IO.Path]::GetFullPath($TargetRawRoot)
    $targetDriveRoot = [System.IO.Path]::GetPathRoot($targetFullPath)
    $compatibility = Compare-TargetCompatibility $sourceManifestBefore $targetFullPath
    $targetBeforeManifest = @($compatibility['target_manifest'])
    $targetBeforeStats = Get-ManifestStats $targetBeforeManifest
    $foreignFilesBefore = @($compatibility['foreign_files'])
    $fVolume = Get-Volume -DriveLetter F -ErrorAction Stop
    $fTotalBytes = [int64]$fVolume.Size
    $fFreeBytes = [int64]$fVolume.SizeRemaining
    $reserveFloorBytes = [int64][Math]::Max([int64][Math]::Ceiling([double]$fTotalBytes * 0.20), [int64](512GB))
    $projectedFreeAfterFullCopy = [int64]($fFreeBytes - [int64]$sourceStatsBefore['total_bytes'])
    Write-Host "target_root=$targetFullPath"
    Write-Host "target_drive_root=$targetDriveRoot"
    Write-Host "target_existing_file_count=$($targetBeforeStats['file_count'])"
    Write-Host "target_existing_total_bytes=$($targetBeforeStats['total_bytes'])"
    Write-Host "target_foreign_file_count=$($foreignFilesBefore.Count)"
    Write-Host "target_filesystem=$($fVolume.FileSystem)"
    Write-Host "target_drive_free_bytes=$fFreeBytes"
    Write-Host "reserve_floor_bytes=$reserveFloorBytes"
    Write-Host "projected_free_after_full_copy_bytes=$projectedFreeAfterFullCopy"

    $blockers = @()
    if ($sourceDriveRoot -ne 'D:\') { $blockers += 'SOURCE_RAW_NOT_ON_D' }
    if ([int64]$sourceStatsBefore['file_count'] -le 0 -or [int64]$sourceStatsBefore['total_bytes'] -le 0) { $blockers += 'SOURCE_RAW_EMPTY' }
    if ($targetDriveRoot -ne 'F:\') { $blockers += 'TARGET_RAW_NOT_ON_F' }
    if ($sourceRoot.TrimEnd('\').Equals($targetFullPath.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)) { $blockers += 'SOURCE_TARGET_SAME_PATH' }
    if ([string]$fVolume.FileSystem -ne 'NTFS') { $blockers += 'TARGET_F_FILESYSTEM_NOT_NTFS' }
    if ($foreignFilesBefore.Count -ne 0) { $blockers += 'TARGET_CONTAINS_FOREIGN_FILES' }
    if ($workerBefore -ne 0) { $blockers += 'WORKER_CONTAINER_COUNT_NOT_ZERO' }
    if (-not [bool]$productionBefore['ready']) { $blockers += 'PRODUCTION_CLICKHOUSE_NOT_HEALTHY' }
    if ($projectedFreeAfterFullCopy -lt $reserveFloorBytes) { $blockers += 'F_HEADROOM_BELOW_RESERVE_AFTER_COPY' }

    $readyForApply = ($blockers.Count -eq 0)
    $copyPerformed = $false
    $robocopyExitCode = $null
    $metadataExact = $false
    $sourceStable = $false
    $hashAttempted = $false
    $hashMismatchCount = [int64]0
    $verifiedFileCount = [int64]0
    $verifiedSourceBytes = [int64]0
    $hashMismatchSample = @()
    $targetAfterManifest = @()
    $postBlockers = @()

    if ($Apply -and $readyForApply) {
        Write-Host 'copy_stage=forward_copy_apply'
        if (-not (Test-Path -LiteralPath $targetFullPath -PathType Container)) {
            New-Item -ItemType Directory -Path $targetFullPath -Force | Out-Null
        }
        $robocopyArgs = @(
            $sourceRoot,
            $targetFullPath,
            '/E',
            '/COPY:DAT',
            '/DCOPY:DAT',
            '/R:1',
            '/W:3',
            '/J',
            '/XJ',
            '/NP',
            '/NFL',
            '/NDL'
        )
        & robocopy.exe @robocopyArgs | Out-Host
        $robocopyExitCode = $LASTEXITCODE
        $copyPerformed = $true
        Write-Host "robocopy_exit_code=$robocopyExitCode"
        if ($robocopyExitCode -gt 7) { $postBlockers += 'ROBOCOPY_FAILED' }

        Write-Host 'copy_stage=metadata_parity'
        $sourceManifestAfter = @(Get-TreeManifest $sourceRoot)
        $targetAfterManifest = @(Get-TreeManifest $targetFullPath)
        $sourceStable = Test-SourceManifestStable $sourceManifestBefore $sourceManifestAfter
        $metadataComparison = Compare-MetadataExact $sourceManifestAfter $targetAfterManifest
        $metadataExact = [bool]$metadataComparison['exact']
        Write-Host "source_manifest_stable=$sourceStable"
        Write-Host "target_file_count_after=$(@($targetAfterManifest).Count)"
        Write-Host "metadata_parity_exact=$metadataExact"
        Write-Host "metadata_missing_count=$(@($metadataComparison['missing']).Count)"
        Write-Host "metadata_size_mismatch_count=$(@($metadataComparison['size_mismatch']).Count)"
        Write-Host "metadata_extra_count=$(@($metadataComparison['extra']).Count)"
        if (-not $sourceStable) { $postBlockers += 'SOURCE_MANIFEST_CHANGED_DURING_COPY' }
        if (-not $metadataExact) { $postBlockers += 'METADATA_PARITY_FAILED' }

        if ($metadataExact -and $sourceStable -and $robocopyExitCode -le 7) {
            Write-Host 'copy_stage=sha256_parity'
            $hashAttempted = $true
            $targetMap = @{}
            foreach ($targetItem in @($targetAfterManifest)) { $targetMap[$targetItem.key] = $targetItem }
            $index = 0
            foreach ($sourceItem in @($sourceManifestAfter)) {
                $index += 1
                $targetItem = $targetMap[$sourceItem.key]
                $sourceHash = (Get-FileHash -LiteralPath $sourceItem.full_path -Algorithm SHA256).Hash
                $targetHash = (Get-FileHash -LiteralPath $targetItem.full_path -Algorithm SHA256).Hash
                if ($sourceHash -ne $targetHash) {
                    $hashMismatchCount += 1
                    if ($hashMismatchSample.Count -lt 20) { $hashMismatchSample += $sourceItem.relative_path }
                }
                else {
                    $verifiedFileCount += 1
                    $verifiedSourceBytes += [int64]$sourceItem.length
                }
                if (($index % 100) -eq 0 -or $index -eq @($sourceManifestAfter).Count) {
                    Write-Host "sha256_progress=$index/$(@($sourceManifestAfter).Count)"
                }
            }
            Write-Host "verified_file_count=$verifiedFileCount"
            Write-Host "verified_source_bytes=$verifiedSourceBytes"
            Write-Host "hash_mismatch_count=$hashMismatchCount"
            if ($hashMismatchCount -ne 0) { $postBlockers += 'SHA256_PARITY_FAILED' }
        }

        Write-Host 'copy_stage=production_invariants_after'
        $workerAfter = Get-WorkerContainerCount
        $productionAfter = Get-ProductionClickHouseHealth
        $envHashAfter = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
        Write-Host "worker_container_count_after=$workerAfter"
        Write-Host "production_clickhouse_ready_after=$($productionAfter['ready'])"
        Write-Host "production_clickhouse_health_after=$($productionAfter['health'])"
        Write-Host "env_unchanged=$($envHashBefore -eq $envHashAfter)"
        if ($workerAfter -ne 0) { $postBlockers += 'WORKER_CONTAINER_COUNT_CHANGED' }
        if (-not [bool]$productionAfter['ready']) { $postBlockers += 'PRODUCTION_CLICKHOUSE_NOT_HEALTHY_AFTER_COPY' }
        if ($envHashBefore -ne $envHashAfter) { $postBlockers += 'ENV_CHANGED_DURING_COPY' }
    }
    elseif ($Apply -and -not $readyForApply) {
        Write-Host 'copy_stage=apply_blocked'
    }

    $allBlockers = @($blockers + $postBlockers)
    $applyAccepted = [bool](
        $Apply -and
        $readyForApply -and
        $copyPerformed -and
        $robocopyExitCode -le 7 -and
        $metadataExact -and
        $sourceStable -and
        $hashAttempted -and
        $hashMismatchCount -eq 0 -and
        $verifiedFileCount -eq [int64]$sourceStatsBefore['file_count'] -and
        $verifiedSourceBytes -eq [int64]$sourceStatsBefore['total_bytes'] -and
        $allBlockers.Count -eq 0
    )

    if ($Apply) {
        $decision = if ($applyAccepted) { 'RAW_FORWARD_COPY_PARITY_GO' } else { 'RAW_FORWARD_COPY_BLOCKED' }
    }
    else {
        $decision = if ($readyForApply) { 'RAW_FORWARD_COPY_READY_FOR_APPLY' } else { 'RAW_FORWARD_COPY_BLOCKED' }
    }

    $receipt = [ordered]@{
        schema='RAW_FORWARD_COPY_V1'
        generated_at_utc=(Get-Date).ToUniversalTime().ToString('o')
        decision=$decision
        apply_requested=[bool]$Apply
        ready_for_apply=$readyForApply
        apply_accepted=$applyAccepted
        source_root=$sourceRoot
        source_file_count=[int64]$sourceStatsBefore['file_count']
        source_total_bytes=[int64]$sourceStatsBefore['total_bytes']
        target_root=$targetFullPath
        target_existing_file_count=[int64]$targetBeforeStats['file_count']
        target_foreign_file_count=[int64]$foreignFilesBefore.Count
        target_drive_total_bytes=$fTotalBytes
        target_drive_free_bytes_before=$fFreeBytes
        reserve_floor_bytes=$reserveFloorBytes
        projected_free_after_full_copy_bytes=$projectedFreeAfterFullCopy
        robocopy_exit_code=$robocopyExitCode
        copy_performed=$copyPerformed
        source_manifest_stable=$sourceStable
        metadata_parity_exact=$metadataExact
        hash_attempted=$hashAttempted
        verified_file_count=$verifiedFileCount
        verified_source_bytes=$verifiedSourceBytes
        hash_mismatch_count=$hashMismatchCount
        hash_mismatch_sample=@($hashMismatchSample)
        blockers=@($allBlockers)
        next_gate=if ($applyAccepted) { 'RAW_DATA_PATH_CUTOVER_PREFLIGHT' } else { 'NONE' }
        source_delete_authorized=$false
        env_change_authorized=$false
        raw_move_authorized=$false
        vhdx_mutation_performed=$false
        wsl_mutation_performed=$false
        docker_restart_performed=$false
        clickhouse_mutation_performed=$false
        corpus_replay_performed=$false
        us_package_2_authorized=$false
        us_bulk_authorized=$false
    }
    $reportPath = Join-Path $evidenceDir 'raw_forward_copy.json'
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host '===== RAW FORWARD-COPY TO F RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "ready_for_apply=$readyForApply"
    Write-Host "apply_accepted=$applyAccepted"
    Write-Host "copy_performed=$copyPerformed"
    Write-Host "source_delete_authorized=False"
    Write-Host "env_change_authorized=False"
    Write-Host "blocker_count=$($allBlockers.Count)"
    foreach ($blocker in $allBlockers) { Write-Host "blocker=$blocker" }
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'RAW_FORWARD_COPY_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
