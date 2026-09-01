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

function Get-DotEnvValues {
    param(
        [Parameter(Mandatory = $true)][string[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*=(.*)$'
    $values = @()
    foreach ($line in $Lines) {
        $match = [regex]::Match($line, $pattern)
        if (-not $match.Success) { continue }
        $value = $match.Groups[1].Value.Trim().Trim('"').Trim("'")
        $values += $value
    }
    return @($values)
}

function Resolve-ConfiguredHostPath {
    param(
        [string]$Value,
        [Parameter(Mandatory = $true)][string]$FallbackRelative
    )
    $candidate = $Value
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = Join-Path $repoRoot $FallbackRelative
    }
    elseif (-not [System.IO.Path]::IsPathRooted($candidate)) {
        $candidate = Join-Path $repoRoot $candidate
    }
    return [System.IO.Path]::GetFullPath($candidate)
}

function Test-PathUnderRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $fullRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    if ($fullPath.Equals($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    return $fullPath.StartsWith($fullRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)
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
    $count = [int64](@($Manifest).Count)
    return [ordered]@{ file_count=$count; total_bytes=$bytes }
}

function Compare-MetadataExact([object[]]$SourceManifest, [object[]]$TargetManifest) {
    $sourceMap = @{}
    foreach ($item in @($SourceManifest)) { $sourceMap[$item.key] = $item }
    $targetMap = @{}
    foreach ($item in @($TargetManifest)) { $targetMap[$item.key] = $item }

    $missing = @()
    $sizeMismatch = @()
    foreach ($source in @($SourceManifest)) {
        if (-not $targetMap.ContainsKey($source.key)) {
            $missing += $source.relative_path
            continue
        }
        if ([int64]$targetMap[$source.key].length -ne [int64]$source.length) {
            $sizeMismatch += $source.relative_path
        }
    }
    $extra = @($TargetManifest | Where-Object { -not $sourceMap.ContainsKey($_.key) } | ForEach-Object { $_.relative_path })
    $exact = [bool](
        $missing.Count -eq 0 -and
        $sizeMismatch.Count -eq 0 -and
        $extra.Count -eq 0 -and
        @($SourceManifest).Count -eq @($TargetManifest).Count
    )
    return [ordered]@{
        exact=$exact
        missing=@($missing)
        size_mismatch=@($sizeMismatch)
        extra=@($extra)
    }
}

function Get-RunningComposeServiceCount([string]$Service) {
    $probe = Invoke-NativeText 'docker' @('compose','ps','-q',$Service) -AllowFailure
    if ($probe['exit_code'] -ne 0) {
        return [ordered]@{ probe_ok=$false; count=[int64]-1 }
    }
    $ids = @($probe['lines'] | Where-Object { $_.Trim() })
    return [ordered]@{ probe_ok=$true; count=[int64]$ids.Count }
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

try {
    Write-Host '===== RAW BIND CUTOVER TO F PREFLIGHT ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'Raw bind cutover preflight must run from local main.'
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Raw bind cutover preflight requires elevated Administrator PowerShell.'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "raw_bind_cutover_preflight_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

    Write-Host 'preflight_stage=env_alias_inventory'
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env is required.' }
    $envHashBefore = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    $envLines = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    $rawValues = @(Get-DotEnvValues -Lines $envLines -Name 'RAW_DATA_PATH')
    $visualRawValues = @(Get-DotEnvValues -Lines $envLines -Name 'VISUAL_RAW_PATH')
    $visualProcessedValues = @(Get-DotEnvValues -Lines $envLines -Name 'VISUAL_PROCESSED_PATH')

    $rawValue = if ($rawValues.Count -eq 1) { [string]$rawValues[0] } else { '' }
    $visualRawValue = if ($visualRawValues.Count -eq 1) { [string]$visualRawValues[0] } else { '' }
    $visualProcessedValue = if ($visualProcessedValues.Count -eq 1) { [string]$visualProcessedValues[0] } else { '' }

    $sourceRoot = Resolve-ConfiguredHostPath -Value $rawValue -FallbackRelative 'raw_data'
    $visualRawRoot = Resolve-ConfiguredHostPath -Value $visualRawValue -FallbackRelative 'raw_data'
    $visualProcessedRoot = Resolve-ConfiguredHostPath -Value $visualProcessedValue -FallbackRelative 'raw_data\visual_processed'
    $targetFullPath = [System.IO.Path]::GetFullPath($TargetRawRoot)
    $proposedComposePath = 'F:/MarkOrbitData/raw'

    Write-Host "raw_data_path_entry_count=$($rawValues.Count)"
    Write-Host "raw_data_path_effective=$sourceRoot"
    Write-Host "visual_raw_path_entry_count=$($visualRawValues.Count)"
    Write-Host "visual_raw_path_effective=$visualRawRoot"
    Write-Host "visual_processed_path_entry_count=$($visualProcessedValues.Count)"
    Write-Host "visual_processed_path_effective=$visualProcessedRoot"
    Write-Host "proposed_RAW_DATA_PATH=$proposedComposePath"
    Write-Host "proposed_VISUAL_RAW_PATH=$proposedComposePath"
    Write-Host 'proposed_VISUAL_PROCESSED_PATH=UNCHANGED'

    Write-Host 'preflight_stage=compose_bind_contract'
    $composePath = Join-Path $repoRoot 'docker-compose.yml'
    $composeText = Get-Content -LiteralPath $composePath -Raw -Encoding UTF8
    $rawBindMarker = '${RAW_DATA_PATH}:/data/raw'
    $visualRawBindMarker = '${VISUAL_RAW_PATH:-./raw_data}:/data/visual-raw'
    $visualProcessedBindMarker = '${VISUAL_PROCESSED_PATH:-./raw_data/visual_processed}:/data/visual-processed'
    $rawBindCount = [regex]::Matches($composeText, [regex]::Escape($rawBindMarker)).Count
    $visualRawBindCount = [regex]::Matches($composeText, [regex]::Escape($visualRawBindMarker)).Count
    $visualProcessedBindCount = [regex]::Matches($composeText, [regex]::Escape($visualProcessedBindMarker)).Count
    Write-Host "compose_raw_bind_count=$rawBindCount"
    Write-Host "compose_visual_raw_bind_count=$visualRawBindCount"
    Write-Host "compose_visual_processed_bind_count=$visualProcessedBindCount"

    Write-Host 'preflight_stage=current_byte_parity'
    if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) { throw "Source Raw root missing: $sourceRoot" }
    if (-not (Test-Path -LiteralPath $targetFullPath -PathType Container)) { throw "Target Raw root missing: $targetFullPath" }
    $sourceManifestBefore = @(Get-TreeManifest $sourceRoot)
    $targetManifestBefore = @(Get-TreeManifest $targetFullPath)
    $sourceStats = Get-ManifestStats $sourceManifestBefore
    $targetStats = Get-ManifestStats $targetManifestBefore
    $metadata = Compare-MetadataExact $sourceManifestBefore $targetManifestBefore
    Write-Host "source_file_count=$($sourceStats['file_count'])"
    Write-Host "source_total_bytes=$($sourceStats['total_bytes'])"
    Write-Host "target_file_count=$($targetStats['file_count'])"
    Write-Host "target_total_bytes=$($targetStats['total_bytes'])"
    Write-Host "metadata_parity_exact=$($metadata['exact'])"
    Write-Host "metadata_missing_count=$(@($metadata['missing']).Count)"
    Write-Host "metadata_size_mismatch_count=$(@($metadata['size_mismatch']).Count)"
    Write-Host "metadata_extra_count=$(@($metadata['extra']).Count)"

    $hashAttempted = $false
    $hashMismatchCount = [int64]0
    $verifiedFileCount = [int64]0
    $verifiedSourceBytes = [int64]0
    $hashMismatchSample = @()
    if ([bool]$metadata['exact']) {
        $hashAttempted = $true
        $targetMap = @{}
        foreach ($targetItem in @($targetManifestBefore)) { $targetMap[$targetItem.key] = $targetItem }
        $index = 0
        foreach ($sourceItem in @($sourceManifestBefore)) {
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
            if (($index % 100) -eq 0 -or $index -eq @($sourceManifestBefore).Count) {
                Write-Host "sha256_progress=$index/$(@($sourceManifestBefore).Count)"
            }
        }
    }
    Write-Host "hash_attempted=$hashAttempted"
    Write-Host "verified_file_count=$verifiedFileCount"
    Write-Host "verified_source_bytes=$verifiedSourceBytes"
    Write-Host "hash_mismatch_count=$hashMismatchCount"

    $sourceManifestAfter = @(Get-TreeManifest $sourceRoot)
    $targetManifestAfter = @(Get-TreeManifest $targetFullPath)
    $sourceStable = [bool](Compare-MetadataExact $sourceManifestBefore $sourceManifestAfter)['exact']
    $targetStable = [bool](Compare-MetadataExact $targetManifestBefore $targetManifestAfter)['exact']
    Write-Host "source_manifest_stable=$sourceStable"
    Write-Host "target_manifest_stable=$targetStable"

    Write-Host 'preflight_stage=runtime_writer_quiescence'
    $rawConsumerServices = @('api','worker','mark-image-worker','qcc-acquisition')
    $runningRawConsumerCount = [int64]0
    $consumerProbeFailed = $false
    $consumerStates = @()
    foreach ($service in $rawConsumerServices) {
        $state = Get-RunningComposeServiceCount $service
        $consumerStates += [ordered]@{
            service=$service
            probe_ok=[bool]$state['probe_ok']
            running_count=[int64]$state['count']
        }
        Write-Host "raw_consumer_service=$service probe_ok=$($state['probe_ok']) running_count=$($state['count'])"
        if (-not [bool]$state['probe_ok']) { $consumerProbeFailed = $true }
        elseif ([int64]$state['count'] -gt 0) { $runningRawConsumerCount += [int64]$state['count'] }
    }
    Write-Host "running_raw_consumer_count=$runningRawConsumerCount"

    Write-Host 'preflight_stage=production_invariants'
    $production = Get-ProductionClickHouseHealth
    $acceptedVolumeProbe = Invoke-NativeText 'docker' @('volume','inspect','markorbit-data-engine_clickhouse_data') -AllowFailure
    $acceptedVolumePresent = ($acceptedVolumeProbe['exit_code'] -eq 0)
    Write-Host "production_clickhouse_ready=$($production['ready'])"
    Write-Host "production_clickhouse_health=$($production['health'])"
    Write-Host "accepted_volume_present=$acceptedVolumePresent"

    $fVolume = Get-Volume -DriveLetter F -ErrorAction Stop
    $sourceDriveRoot = [System.IO.Path]::GetPathRoot($sourceRoot)
    $targetDriveRoot = [System.IO.Path]::GetPathRoot($targetFullPath)
    $visualRawMatchesSource = $visualRawRoot.TrimEnd('\').Equals($sourceRoot.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)
    $visualProcessedUnderLegacyRaw = Test-PathUnderRoot -Path $visualProcessedRoot -Root $sourceRoot
    Write-Host "source_drive_root=$sourceDriveRoot"
    Write-Host "target_drive_root=$targetDriveRoot"
    Write-Host "target_filesystem=$($fVolume.FileSystem)"
    Write-Host "visual_raw_matches_legacy_raw=$visualRawMatchesSource"
    Write-Host "visual_processed_under_legacy_raw=$visualProcessedUnderLegacyRaw"

    $envHashAfter = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    $envUnchanged = ($envHashBefore -eq $envHashAfter)
    Write-Host "env_unchanged=$envUnchanged"

    $blockers = @()
    if ($rawValues.Count -ne 1) { $blockers += 'RAW_DATA_PATH_ENTRY_COUNT_NOT_ONE' }
    if ($visualRawValues.Count -gt 1) { $blockers += 'VISUAL_RAW_PATH_ENTRY_COUNT_GT_ONE' }
    if ($visualProcessedValues.Count -gt 1) { $blockers += 'VISUAL_PROCESSED_PATH_ENTRY_COUNT_GT_ONE' }
    if ($sourceDriveRoot -ne 'D:\') { $blockers += 'LEGACY_RAW_SOURCE_NOT_ON_D' }
    if ($targetDriveRoot -ne 'F:\') { $blockers += 'TARGET_RAW_NOT_ON_F' }
    if ([string]$fVolume.FileSystem -ne 'NTFS') { $blockers += 'TARGET_F_FILESYSTEM_NOT_NTFS' }
    if (-not $visualRawMatchesSource) { $blockers += 'VISUAL_RAW_NOT_ALIAS_OF_LEGACY_RAW' }
    if ($rawBindCount -ne 4) { $blockers += 'COMPOSE_RAW_BIND_CONTRACT_DRIFT' }
    if ($visualRawBindCount -ne 3) { $blockers += 'COMPOSE_VISUAL_RAW_BIND_CONTRACT_DRIFT' }
    if ($visualProcessedBindCount -ne 3) { $blockers += 'COMPOSE_VISUAL_PROCESSED_BIND_CONTRACT_DRIFT' }
    if (-not [bool]$metadata['exact']) { $blockers += 'CURRENT_METADATA_PARITY_FAILED' }
    if (-not $hashAttempted -or $hashMismatchCount -ne 0) { $blockers += 'CURRENT_SHA256_PARITY_FAILED' }
    if ($verifiedFileCount -ne [int64]$sourceStats['file_count'] -or $verifiedSourceBytes -ne [int64]$sourceStats['total_bytes']) { $blockers += 'CURRENT_SHA256_VERIFIED_TOTALS_MISMATCH' }
    if (-not $sourceStable) { $blockers += 'SOURCE_MANIFEST_CHANGED_DURING_PREFLIGHT' }
    if (-not $targetStable) { $blockers += 'TARGET_MANIFEST_CHANGED_DURING_PREFLIGHT' }
    if ($consumerProbeFailed) { $blockers += 'RAW_CONSUMER_CONTAINER_PROBE_FAILED' }
    if ($runningRawConsumerCount -ne 0) { $blockers += 'RAW_CONSUMER_CONTAINER_RUNNING' }
    if (-not [bool]$production['ready']) { $blockers += 'PRODUCTION_CLICKHOUSE_NOT_HEALTHY' }
    if (-not $acceptedVolumePresent) { $blockers += 'ACCEPTED_CLICKHOUSE_VOLUME_MISSING' }
    if (-not $envUnchanged) { $blockers += 'ENV_CHANGED_DURING_PREFLIGHT' }

    $deleteBlockers = @('RAW_BIND_CUTOVER_NOT_YET_APPLIED')
    if ($visualProcessedUnderLegacyRaw) { $deleteBlockers += 'VISUAL_PROCESSED_PATH_UNDER_LEGACY_D_RAW' }

    $ready = ($blockers.Count -eq 0)
    $decision = if ($ready) { 'RAW_BIND_CUTOVER_PREFLIGHT_READY' } else { 'RAW_BIND_CUTOVER_PREFLIGHT_BLOCKED' }
    $nextGate = if ($ready) { 'JOINT_RAW_BIND_CUTOVER_APPLY' } else { 'NONE' }

    $receipt = [ordered]@{
        schema='RAW_BIND_CUTOVER_PREFLIGHT_V1'
        generated_at_utc=(Get-Date).ToUniversalTime().ToString('o')
        decision=$decision
        ready_for_joint_raw_cutover=$ready
        source_root=$sourceRoot
        target_root=$targetFullPath
        source_file_count=[int64]$sourceStats['file_count']
        source_total_bytes=[int64]$sourceStats['total_bytes']
        target_file_count=[int64]$targetStats['file_count']
        target_total_bytes=[int64]$targetStats['total_bytes']
        metadata_parity_exact=[bool]$metadata['exact']
        hash_attempted=$hashAttempted
        verified_file_count=$verifiedFileCount
        verified_source_bytes=$verifiedSourceBytes
        hash_mismatch_count=$hashMismatchCount
        hash_mismatch_sample=@($hashMismatchSample)
        source_manifest_stable=$sourceStable
        target_manifest_stable=$targetStable
        raw_data_path_entry_count=[int64]$rawValues.Count
        visual_raw_path_entry_count=[int64]$visualRawValues.Count
        visual_processed_path_entry_count=[int64]$visualProcessedValues.Count
        visual_raw_effective_root=$visualRawRoot
        visual_processed_effective_root=$visualProcessedRoot
        visual_raw_matches_legacy_raw=$visualRawMatchesSource
        visual_processed_under_legacy_raw=$visualProcessedUnderLegacyRaw
        proposed_RAW_DATA_PATH=$proposedComposePath
        proposed_VISUAL_RAW_PATH=$proposedComposePath
        proposed_VISUAL_PROCESSED_PATH='UNCHANGED'
        compose_raw_bind_count=[int64]$rawBindCount
        compose_visual_raw_bind_count=[int64]$visualRawBindCount
        compose_visual_processed_bind_count=[int64]$visualProcessedBindCount
        raw_consumers=@($consumerStates)
        running_raw_consumer_count=$runningRawConsumerCount
        production_clickhouse_ready=[bool]$production['ready']
        accepted_volume_present=$acceptedVolumePresent
        blockers=@($blockers)
        d_source_delete_blockers=@($deleteBlockers)
        next_gate=$nextGate
        env_change_authorized=$false
        docker_recreate_authorized=$false
        raw_delete_authorized=$false
        raw_move_authorized=$false
        visual_processed_migration_authorized=$false
        vhdx_mutation_performed=$false
        wsl_mutation_performed=$false
        docker_restart_performed=$false
        clickhouse_mutation_performed=$false
        corpus_replay_performed=$false
        us_package_2_authorized=$false
        us_bulk_authorized=$false
    }
    $reportPath = Join-Path $evidenceDir 'raw_bind_cutover_preflight.json'
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host '===== RAW BIND CUTOVER TO F PREFLIGHT RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "ready_for_joint_raw_cutover=$ready"
    Write-Host "next_gate=$nextGate"
    Write-Host 'env_change_authorized=False'
    Write-Host 'docker_recreate_authorized=False'
    Write-Host 'raw_delete_authorized=False'
    Write-Host "blocker_count=$($blockers.Count)"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host "d_source_delete_blocker_count=$($deleteBlockers.Count)"
    foreach ($deleteBlocker in $deleteBlockers) { Write-Host "d_source_delete_blocker=$deleteBlocker" }
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'RAW_BIND_CUTOVER_PREFLIGHT_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
