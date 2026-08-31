param(
    [string]$AcceptedHotPath = "E:\MarkOrbitData\hot\clickhouse-cs",
    [string]$RejectedLegacyHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    function Resolve-WindowsDirectory([string]$Path, [string]$Name) {
        if ([string]::IsNullOrWhiteSpace($Path)) { throw "$Name is required." }
        $candidate = $Path.Replace('/', '\')
        if ($candidate -notmatch '^[A-Za-z]:\\') { throw "$Name is not an absolute Windows path: $Path" }
        if (-not (Test-Path -LiteralPath $candidate -PathType Container)) { throw "$Name does not exist: $candidate" }
        return (Resolve-Path -LiteralPath $candidate).Path.TrimEnd('\')
    }

    $accepted = Resolve-WindowsDirectory $AcceptedHotPath "AcceptedHotPath"
    $legacy = $RejectedLegacyHotPath.Replace('/', '\').TrimEnd('\')

    $ids = @(& docker compose ps --status running -q clickhouse | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
        throw "Exactly one running ClickHouse container is required."
    }
    $mountsJson = (& docker inspect --format '{{json .Mounts}}' $ids[0]).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($mountsJson)) {
        throw "Unable to inspect ClickHouse mounts."
    }
    $mounts = $mountsJson | ConvertFrom-Json
    $matches = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    if ($matches.Count -ne 1) { throw "Expected exactly one /var/lib/clickhouse mount." }
    $mount = $matches[0]
    $actualRaw = [string]$mount.Source
    $actual = Resolve-WindowsDirectory $actualRaw "ActiveHotMountSource"
    $rw = [bool]$mount.RW
    $type = [string]$mount.Type

    $caseOutput = @(& fsutil.exe file queryCaseSensitiveInfo $actual 2>&1 | ForEach-Object { $_.ToString() })
    $caseExit = $LASTEXITCODE
    $caseText = ($caseOutput -join " ").Trim()
    $caseDisabled = $caseText -match '(?i)\bdisabled\b|已禁用|未启用|禁用'
    $caseEnabled = (-not $caseDisabled) -and ($caseText -match '(?i)\benabled\b|已启用|启用')

    $legacyActive = $actual.Equals($legacy, [System.StringComparison]::OrdinalIgnoreCase)
    $acceptedActive = $actual.Equals($accepted, [System.StringComparison]::OrdinalIgnoreCase)
    $blockers = @()
    if ($type -ne 'bind') { $blockers += 'ACTIVE_CLICKHOUSE_DATA_NOT_BIND_MOUNT' }
    if (-not $rw) { $blockers += 'ACTIVE_CLICKHOUSE_DATA_NOT_RW' }
    if ($legacyActive) { $blockers += 'REJECTED_LEGACY_CASE_INSENSITIVE_HOT_PATH' }
    if (-not $acceptedActive) { $blockers += 'ACTIVE_HOT_SOURCE_NOT_ACCEPTED_CLICKHOUSE_CS' }
    if ($caseExit -ne 0 -or -not $caseEnabled) { $blockers += 'ACTIVE_HOT_CASE_SENSITIVITY_NOT_ENABLED' }

    $report = [ordered]@{
        report_version = 'CLICKHOUSE_ACTIVE_HOT_STORAGE_CONTRACT_V1'
        accepted_hot_path = $accepted
        rejected_legacy_hot_path = $legacy
        actual_mount_source = $actual
        mount_type = $type
        mount_rw = $rw
        accepted_mount_active = $acceptedActive
        rejected_legacy_mount_active = $legacyActive
        case_sensitive_query_exit_code = $caseExit
        case_sensitive_enabled = $caseEnabled
        case_sensitive_query_output = $caseOutput
        blockers = @($blockers)
        safe_for_clickhouse_merge_tree_writes = ($blockers.Count -eq 0)
        filesystem_mutation_performed = $false
        compose_mutation_performed = $false
        schema_apply_performed = $false
        corpus_replay_performed = $false
    }
    if ($OutputPath) {
        $parent = Split-Path -Parent $OutputPath
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        $report | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $OutputPath
    }

    Write-Host "accepted_hot_path=$accepted"
    Write-Host "actual_hot_mount_source=$actual"
    Write-Host "active_hot_mount_type=$type"
    Write-Host "active_hot_mount_rw=$rw"
    Write-Host "accepted_mount_active=$acceptedActive"
    Write-Host "rejected_legacy_mount_active=$legacyActive"
    Write-Host "active_hot_case_sensitive_enabled=$caseEnabled"
    Write-Host "active_hot_storage_blockers=$($blockers.Count)"
    foreach ($blocker in $blockers) { Write-Host "active_hot_storage_blocker=$blocker" }
    Write-Host "filesystem_mutation_performed=False"
    Write-Host "compose_mutation_performed=False"
    Write-Host "schema_apply_performed=False"
    Write-Host "corpus_replay_performed=False"

    if ($blockers.Count -ne 0) {
        throw "Active ClickHouse Hot storage contract is not accepted: $($blockers -join ', ')"
    }
    Write-Host "CLICKHOUSE_ACTIVE_HOT_STORAGE_CONTRACT_PASS"
}
finally {
    Pop-Location
}
