[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
    [string]$EvidenceRoot = 'reports'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments,
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

function Resolve-LxClickHouseTarget([string]$CandidateRoot, [string]$RawTarget) {
    $candidate = Normalize-HostPath $CandidateRoot
    $prefix = '/var/lib/clickhouse/'
    $result = [ordered]@{
        raw_target=$RawTarget
        accepted_linux_prefix=$false
        linux_prefix=$prefix
        suffix=''
        segment_count=0
        segments_valid=$false
        mapped_target=''
        target_exists=$false
        target_is_directory=$false
        target_is_reparse_point=$false
        mapped_target_inside_candidate_root=$false
        mapping_error=''
    }
    if (-not $candidate) { $result.mapping_error='CANDIDATE_ROOT_INVALID'; return $result }
    if ([string]::IsNullOrWhiteSpace($RawTarget)) { $result.mapping_error='LX_TARGET_EMPTY'; return $result }

    $target = $RawTarget.Trim()
    if (-not $target.StartsWith($prefix, [System.StringComparison]::Ordinal)) {
        $result.mapping_error='LX_TARGET_PREFIX_REJECTED'
        return $result
    }
    $result.accepted_linux_prefix=$true
    $suffix = $target.Substring($prefix.Length).TrimEnd('/')
    $result.suffix=$suffix
    if ([string]::IsNullOrWhiteSpace($suffix)) { $result.mapping_error='LX_TARGET_ROOT_SELF_REFERENCE'; return $result }
    if ($suffix.Contains('\') -or $suffix.Contains('//')) { $result.mapping_error='LX_TARGET_PATH_INVALID'; return $result }

    $segments = @($suffix.Split('/'))
    $result.segment_count=$segments.Count
    foreach ($segment in $segments) {
        if ([string]::IsNullOrWhiteSpace($segment) -or $segment -eq '.' -or $segment -eq '..' -or $segment -match '[\\:*?"<>|]') {
            $result.mapping_error='LX_TARGET_PATH_INVALID'
            return $result
        }
    }
    $result.segments_valid=$true

    $mapped = $candidate
    foreach ($segment in $segments) { $mapped = Join-Path $mapped $segment }
    try { $mapped = Normalize-HostPath ([System.IO.Path]::GetFullPath($mapped)) }
    catch { $result.mapping_error='LX_TARGET_HOST_NORMALIZATION_FAILED'; return $result }
    if (-not $mapped) { $result.mapping_error='LX_TARGET_HOST_NORMALIZATION_FAILED'; return $result }
    $result.mapped_target=$mapped
    $inside = Test-PathContains $candidate $mapped
    $result.mapped_target_inside_candidate_root=$inside
    if (-not $inside -or $mapped.Equals($candidate, [System.StringComparison]::OrdinalIgnoreCase)) {
        $result.mapping_error='LX_TARGET_ESCAPES_CANDIDATE_ROOT'
        return $result
    }

    $exists = Test-Path -LiteralPath $mapped
    $result.target_exists=[bool]$exists
    if (-not $exists) { $result.mapping_error='LX_TARGET_MISSING'; return $result }
    try {
        $attributes = [System.IO.File]::GetAttributes($mapped)
        $result.target_is_directory=[bool](($attributes -band [System.IO.FileAttributes]::Directory) -ne 0)
        $result.target_is_reparse_point=[bool](($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
    }
    catch { $result.mapping_error='LX_TARGET_ATTRIBUTE_QUERY_FAILED'; return $result }
    if (-not $result.target_is_directory) { $result.mapping_error='LX_TARGET_NOT_DIRECTORY'; return $result }
    if ($result.target_is_reparse_point) { $result.mapping_error='LX_TARGET_IS_REPARSE_POINT'; return $result }
    return $result
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
    catch { throw 'Production ClickHouse mount inspection returned invalid JSON.' }
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
    if ($runningTotal -ne 0) { throw "All Raw consumer services must be absent/stopped; observed $runningTotal." }
}

try {
    Write-Host '===== PRODUCTION REBALANCE E LX TARGET MAPPING ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'E LX target mapping must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'E LX target mapping requires elevated Administrator PowerShell.' }

    $hot = Normalize-HostPath $LegacyEHotRoot
    $logs = Normalize-HostPath $LegacyEHotLogsRoot
    if (-not $hot.Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotRoot must remain the exact approved legacy E ClickHouse root.' }
    if (-not $logs.Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotLogsRoot must remain the exact approved legacy E ClickHouse log root.' }

    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_before=$([bool]$productionBefore.ready)"
    Write-Host "production_clickhouse_health_before=$($productionBefore.health)"
    if (-not [bool]$productionBefore.ready) { throw 'Production ClickHouse must be healthy before LX mapping.' }
    Assert-AcceptedProductionMount $productionBefore.container_id

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $childEvidenceRoot = Join-Path $EvidenceRoot ("_lxmap_native_${timestamp}_$PID")
    $nativeScript = Join-Path $PSScriptRoot 'profile-production-rebalance-e-reparse-native-provenance.ps1'
    Write-Host 'lx_mapping_stage=fresh_native_reparse_receipt'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $nativeScript `
        -ExpectedMainSha $ExpectedMainSha `
        -AcceptedVolume $AcceptedVolume `
        -LegacyEHotRoot $LegacyEHotRoot `
        -LegacyEHotLogsRoot $LegacyEHotLogsRoot `
        -EvidenceRoot $childEvidenceRoot | Out-Host
    $childExitCode = $LASTEXITCODE
    if ($childExitCode -ne 0) { throw "Fresh native reparse provenance exited $childExitCode." }

    $childBase = Join-Path $repoRoot $childEvidenceRoot
    $nativeReceipts = @(Get-ChildItem -LiteralPath $childBase -Filter 'production_rebalance_e_native_reparse_provenance.json' -File -Recurse)
    if ($nativeReceipts.Count -ne 1) { throw "Expected exactly one fresh native reparse receipt; found $($nativeReceipts.Count)." }
    $nativeReceiptPath = $nativeReceipts[0].FullName
    $nativeReceipt = Get-Content -LiteralPath $nativeReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$nativeReceipt.receipt_version -ne 'PRODUCTION_REBALANCE_E_NATIVE_REPARSE_PROVENANCE_V2') { throw 'Unexpected native reparse receipt version.' }
    if ([string]$nativeReceipt.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Fresh native reparse receipt SHA mismatch.' }
    if (-not [bool]$nativeReceipt.read_only -or -not [bool]$nativeReceipt.non_traversing_inventory) { throw 'Fresh native receipt lost read-only/non-traversing contract.' }
    if (-not [bool]$nativeReceipt.env_unchanged) { throw 'Fresh native reparse receipt reports .env mutation.' }

    Write-Host 'lx_mapping_stage=frozen_linux_host_mapping'
    $allPoints = @($nativeReceipt.legacy_e_hot.reparse_points) + @($nativeReceipt.legacy_e_logs.reparse_points)
    $mappedPoints = @()
    foreach ($point in $allPoints) {
        $nativeKind = [string]$point.native_kind
        $nativeError = [string]$point.native_error
        $nativeWin32Error = [int]$point.native_win32_error
        $lxVersion = [uint32]$point.lx_version
        $rawTarget = [string]$point.raw_target
        $mapping = Resolve-LxClickHouseTarget $hot $rawTarget
        $nativeReady = [bool]($nativeKind -eq 'LX_SYMLINK' -and [string]::IsNullOrWhiteSpace($nativeError) -and $nativeWin32Error -eq 0)
        $versionReady = [bool]($lxVersion -eq 2)
        $mappingReady = [bool]($nativeReady -and $versionReady -and [string]::IsNullOrWhiteSpace([string]$mapping.mapping_error) -and [bool]$mapping.target_exists -and [bool]$mapping.target_is_directory -and -not [bool]$mapping.target_is_reparse_point -and [bool]$mapping.mapped_target_inside_candidate_root)
        $entry = [ordered]@{
            path=[string]$point.path
            native_kind=$nativeKind
            native_error=$nativeError
            native_win32_error=$nativeWin32Error
            lx_version=$lxVersion
            raw_target=$rawTarget
            native_ready=$nativeReady
            version_ready=$versionReady
            accepted_linux_prefix=[bool]$mapping.accepted_linux_prefix
            mapped_target=[string]$mapping.mapped_target
            target_exists=[bool]$mapping.target_exists
            target_is_directory=[bool]$mapping.target_is_directory
            target_is_reparse_point=[bool]$mapping.target_is_reparse_point
            mapped_target_inside_candidate_root=[bool]$mapping.mapped_target_inside_candidate_root
            mapping_error=[string]$mapping.mapping_error
            mapping_ready=$mappingReady
        }
        $mappedPoints += $entry
        Write-Host "lx_mapping_path=$($entry.path)"
        Write-Host "lx_mapping_version=$($entry.lx_version)"
        Write-Host "lx_mapping_raw_target=$($entry.raw_target)"
        Write-Host "lx_mapping_mapped_target=$($entry.mapped_target)"
        Write-Host "lx_mapping_target_exists=$($entry.target_exists)"
        Write-Host "lx_mapping_target_is_reparse_point=$($entry.target_is_reparse_point)"
        Write-Host "lx_mapping_inside_candidate_root=$($entry.mapped_target_inside_candidate_root)"
        Write-Host "lx_mapping_error=$($entry.mapping_error)"
        Write-Host "lx_mapping_ready=$($entry.mapping_ready)"
    }

    $nativeBlocked = @($mappedPoints | Where-Object { -not [bool]$_.native_ready }).Count
    $versionBlocked = @($mappedPoints | Where-Object { [bool]$_.native_ready -and -not [bool]$_.version_ready }).Count
    $prefixBlocked = @($mappedPoints | Where-Object { [bool]$_.native_ready -and [bool]$_.version_ready -and -not [bool]$_.accepted_linux_prefix }).Count
    $missingTargets = @($mappedPoints | Where-Object { [string]$_.mapping_error -eq 'LX_TARGET_MISSING' }).Count
    $reparseTargets = @($mappedPoints | Where-Object { [bool]$_.target_is_reparse_point }).Count
    $outsideTargets = @($mappedPoints | Where-Object { $_.mapped_target -and -not [bool]$_.mapped_target_inside_candidate_root }).Count
    $mappingReadyCount = @($mappedPoints | Where-Object { [bool]$_.mapping_ready }).Count
    $mappingErrorCount = @($mappedPoints | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_.mapping_error) }).Count

    if ($allPoints.Count -eq 0) { $decision='REBALANCE_E_LX_MAPPING_NO_REPARSE_POINTS' }
    elseif ($nativeBlocked -gt 0) { $decision='REBALANCE_E_LX_MAPPING_NATIVE_METADATA_BLOCKED' }
    elseif ($versionBlocked -gt 0) { $decision='REBALANCE_E_LX_MAPPING_UNSUPPORTED_VERSION' }
    elseif ($prefixBlocked -gt 0) { $decision='REBALANCE_E_LX_MAPPING_PREFIX_REJECTED' }
    elseif ($missingTargets -gt 0) { $decision='REBALANCE_E_LX_MAPPING_TARGET_MISSING' }
    elseif ($reparseTargets -gt 0) { $decision='REBALANCE_E_LX_MAPPING_TARGET_REPARSE_BLOCKED' }
    elseif ($outsideTargets -gt 0) { $decision='REBALANCE_E_LX_MAPPING_ESCAPES_ROOT' }
    elseif ($mappingErrorCount -gt 0 -or $mappingReadyCount -ne $allPoints.Count) { $decision='REBALANCE_E_LX_MAPPING_BLOCKED' }
    else { $decision='REBALANCE_E_LX_MAPPING_INTERNAL_TARGETS' }

    $nextGate = if ($decision -eq 'REBALANCE_E_LX_MAPPING_INTERNAL_TARGETS') { 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_DESIGN' }
        elseif ($decision -eq 'REBALANCE_E_LX_MAPPING_NO_REPARSE_POINTS') { 'PRODUCTION_REBALANCE_PHASE1_E_DRY_RUN_RETRY' }
        else { 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_REVIEW_REQUIRED' }

    $productionAfter = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_after=$([bool]$productionAfter.ready)"
    Write-Host "production_clickhouse_health_after=$($productionAfter.health)"
    if (-not [bool]$productionAfter.ready) { throw 'Production ClickHouse must remain healthy after LX mapping.' }
    Assert-AcceptedProductionMount $productionAfter.container_id
    Assert-RawConsumersStopped
    $envHashAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    $envUnchanged = [bool]($envHashBefore -eq $envHashAfter)
    if (-not $envUnchanged) { throw '.env changed during read-only LX target mapping.' }

    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_e_lx_target_mapping_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receipt = [ordered]@{
        receipt_version='PRODUCTION_REBALANCE_E_LX_TARGET_MAPPING_V1'
        decision=$decision
        next_gate=$nextGate
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        read_only=$true
        mapping_contract=[ordered]@{
            accepted_linux_prefix='/var/lib/clickhouse/'
            mapped_host_root=$hot
            required_native_kind='LX_SYMLINK'
            required_lx_version=2
            require_target_exists=$true
            require_target_directory=$true
            require_target_not_reparse_point=$true
            require_target_inside_candidate_root=$true
        }
        fresh_native_receipt_path=$nativeReceiptPath
        native_receipt_decision=[string]$nativeReceipt.decision
        point_count=[int64]$allPoints.Count
        native_blocked_count=[int64]$nativeBlocked
        version_blocked_count=[int64]$versionBlocked
        prefix_blocked_count=[int64]$prefixBlocked
        missing_target_count=[int64]$missingTargets
        reparse_target_count=[int64]$reparseTargets
        outside_target_count=[int64]$outsideTargets
        mapping_error_count=[int64]$mappingErrorCount
        mapping_ready_count=[int64]$mappingReadyCount
        points=@($mappedPoints)
        production=[ordered]@{
            clickhouse_ready_before=[bool]$productionBefore.ready
            clickhouse_ready_after=[bool]$productionAfter.ready
            accepted_volume=$AcceptedVolume
            accepted_production_mount_ready=$true
            running_raw_consumer_count=0
        }
        constraints=[ordered]@{
            phase1_delete_authorized=$false
            reparse_delete_authorized=$false
            legacy_e_hot_delete_authorized=$false
            legacy_e_logs_delete_authorized=$false
            accepted_volume_delete_authorized=$false
            accepted_volume_move_authorized=$false
            vhdx_create_authorized=$false
            vhdx_delete_authorized=$false
            vhdx_move_authorized=$false
            wsl_shutdown_authorized=$false
            wsl_unmount_authorized=$false
            docker_restart_authorized=$false
            docker_prune_authorized=$false
            clickhouse_mutation_authorized=$false
            cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
            mutation_performed=$false
        }
        env_unchanged=$envUnchanged
    }
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $evidenceDir 'production_rebalance_e_lx_target_mapping.json') -Encoding UTF8

    Write-Host '===== PRODUCTION REBALANCE E LX TARGET MAPPING RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "point_count=$([int64]$allPoints.Count)"
    Write-Host "native_blocked_count=$nativeBlocked"
    Write-Host "version_blocked_count=$versionBlocked"
    Write-Host "prefix_blocked_count=$prefixBlocked"
    Write-Host "missing_target_count=$missingTargets"
    Write-Host "reparse_target_count=$reparseTargets"
    Write-Host "outside_target_count=$outsideTargets"
    Write-Host "mapping_error_count=$mappingErrorCount"
    Write-Host "mapping_ready_count=$mappingReadyCount"
    Write-Host 'phase1_delete_authorized=False'
    Write-Host 'reparse_delete_authorized=False'
    Write-Host 'mutation_performed=False'
    Write-Host "production_invariant_preserved=$([bool]($productionBefore.ready -and $productionAfter.ready))"
    Write-Host "env_unchanged=$envUnchanged"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_E_LX_TARGET_MAPPING_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
