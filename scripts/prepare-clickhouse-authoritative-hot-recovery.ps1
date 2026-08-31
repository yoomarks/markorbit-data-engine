param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$SourceHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$RecoveryHotPath = "E:\MarkOrbitData\hot\clickhouse-cs",
    [string]$ExpectedColdPath = "F:\MarkOrbitData\cold\clickhouse",
    [string]$ExpectedLogPath = "E:\MarkOrbitData\hot\clickhouse-logs",
    [string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545",
    [int]$ReserveGiB = 128,
    [string]$EvidenceRoot = "reports"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Normalize-WindowsPath([string]$Path, [string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "$Name is required." }
    $candidate = $Path.Replace('/', '\')
    if ($candidate -notmatch '^[A-Za-z]:\\') { throw "$Name must be an absolute Windows path: $Path" }
    return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
}

function Normalize-ComparablePath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return "" }
    return (Normalize-WindowsPath $Path 'ComparablePath').ToLowerInvariant()
}

function Invoke-DockerText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& docker @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $rendered = @($output | ForEach-Object { $_.ToString() })
    if ($exitCode -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $exitCode`: $($rendered -join [Environment]::NewLine)"
    }
    return $rendered
}

function ConvertTo-Base64Utf8([string]$Text) {
    $Text = $Text.Replace("`r`n", "`n").Replace("`r", "`n")
    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Text))
}

function New-QuoteSafeShellRunner([string]$Script) {
    $payload = ConvertTo-Base64Utf8 $Script
    return "printf %s $payload | base64 -d | sh"
}

function Invoke-ComposeScript([string]$Service, [string]$Script) {
    $runner = New-QuoteSafeShellRunner $Script
    return Invoke-DockerText -Arguments @("compose", "exec", "-T", $Service, "sh", "-c", $runner)
}

function Invoke-DockerRunScript([string[]]$RunArguments, [string]$Image, [string]$Script) {
    $runner = New-QuoteSafeShellRunner $Script
    return Invoke-DockerText -Arguments (@("run") + $RunArguments + @("--entrypoint", "sh", $Image, "-c", $runner))
}

function Get-SingleMountByDestination([object]$Mounts, [string]$Destination) {
    $result = $null
    foreach ($mount in $Mounts) {
        if ([string]$mount.Destination -ne $Destination) { continue }
        if ($null -ne $result) { throw "Multiple $Destination mounts found." }
        $result = $mount
    }
    if ($null -eq $result) { throw "Required mount missing: $Destination" }
    return $result
}

function Get-CaseSensitivity([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return [ordered]@{ exists = $false; enabled = $null; exit_code = $null; output = @() }
    }
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $lines = @(& fsutil.exe file queryCaseSensitiveInfo $Path 2>&1 | ForEach-Object { $_.ToString() })
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($exitCode -ne 0) { throw "Unable to query case sensitivity for $Path`: $($lines -join ' ')" }
    $text = ($lines -join ' ').Trim()
    $disabled = $text -match '(?i)\bdisabled\b|已禁用|未启用|禁用'
    $enabled = (-not $disabled) -and ($text -match '(?i)\benabled\b|已启用|启用')
    if (-not $disabled -and -not $enabled) { throw "Unable to classify case sensitivity for $Path`: $text" }
    return [ordered]@{ exists = $true; enabled = [bool]$enabled; exit_code = $exitCode; output = $lines }
}

function Get-CaseChain([string]$Root) {
    $schemaRelative = 'store\771\7716c662-1886-4e4b-a7e2-631c80ac8dd2'
    $paths = @(
        [ordered]@{ label = 'root'; path = $Root },
        [ordered]@{ label = 'metadata'; path = (Join-Path $Root 'metadata') },
        [ordered]@{ label = 'store'; path = (Join-Path $Root 'store') },
        [ordered]@{ label = 'store_prefix'; path = (Join-Path (Join-Path $Root 'store') '771') },
        [ordered]@{ label = 'schema_version_uuid'; path = (Join-Path $Root $schemaRelative) }
    )
    $rows = @()
    foreach ($item in $paths) {
        $case = Get-CaseSensitivity $item.path
        $rows += [pscustomobject][ordered]@{
            label = $item.label
            path = $item.path
            exists = [bool]$case.exists
            case_sensitive = $case.enabled
        }
    }
    return $rows
}

function Get-TextSha256([object[]]$Lines) {
    $text = (@($Lines | ForEach-Object { $_.ToString().TrimEnd() }) -join "`n")
    $bytes = [Text.Encoding]::UTF8.GetBytes($text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-LocalEnvHotPath {
    $envFile = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        return [ordered]@{ exists = $false; match_count = 0; value = $null }
    }
    $values = @()
    foreach ($line in @(Get-Content -LiteralPath $envFile -Encoding UTF8)) {
        if ($line -match '^\s*CLICKHOUSE_HOT_DATA_PATH\s*=\s*(.*?)\s*$') {
            $values += $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return [ordered]@{
        exists = $true
        match_count = $values.Count
        value = if ($values.Count -eq 1) { $values[0] } else { $null }
    }
}

try {
    Write-Host '===== EXACT-MAIN AUTHORITATIVE HOT RECOVERY PREFLIGHT ====='
    if (git status --porcelain) { throw 'Working tree must be clean.' }
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Recovery preflight must run from main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed.' }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw 'Exact-main mismatch.' }

    Write-Host 'preflight_stage=global_idle_zero_worker'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop-idle-worker.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Global idle gate failed.' }
    $workerAll = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($workerAll.Count -ne 0) { throw 'Worker containers must be absent for storage recovery preparation.' }
    Write-Host "worker_container_count_all_states=$($workerAll.Count)"

    $source = Normalize-WindowsPath $SourceHotPath 'SourceHotPath'
    $destination = Normalize-WindowsPath $RecoveryHotPath 'RecoveryHotPath'
    $coldExpected = Normalize-WindowsPath $ExpectedColdPath 'ExpectedColdPath'
    $logsExpected = Normalize-WindowsPath $ExpectedLogPath 'ExpectedLogPath'
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Authoritative source Hot directory missing: $source" }
    if (Test-Path -LiteralPath $destination) { throw "Recovery destination must remain absent during preflight: $destination" }
    if (-not [string]::Equals((Split-Path -Parent $source), (Split-Path -Parent $destination), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source and recovery Hot roots must share the same parent/drive.'
    }

    Write-Host 'preflight_stage=inspect_runtime_mounts'
    $clickhouseIds = @(Invoke-DockerText -Arguments @('compose','ps','--status','running','-q','clickhouse') |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($clickhouseIds.Count -ne 1) { throw 'Exactly one running ClickHouse container is required.' }
    $cid = $clickhouseIds[0]
    $health = ((Invoke-DockerText -Arguments @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$cid)) -join '').Trim()
    if ($health -ne 'healthy') { throw "ClickHouse must be healthy for preflight; observed=$health" }
    $image = ((Invoke-DockerText -Arguments @('inspect','--format','{{.Config.Image}}',$cid)) -join '').Trim()
    if ($image -notmatch ':24\.8(?:$|[.-])') { throw "Unexpected ClickHouse image for frozen recovery path: $image" }
    $mountsJson = ((Invoke-DockerText -Arguments @('inspect','--format','{{json .Mounts}}',$cid)) -join '').Trim()
    $mounts = $mountsJson | ConvertFrom-Json
    $hotMount = Get-SingleMountByDestination $mounts '/var/lib/clickhouse'
    $coldMount = Get-SingleMountByDestination $mounts '/var/lib/clickhouse-cold'
    $logMount = Get-SingleMountByDestination $mounts '/var/log/clickhouse-server'
    if ([string]$hotMount.Type -ne 'bind' -or -not [bool]$hotMount.RW) { throw 'Active Hot mount must be a read/write bind.' }
    if ((Normalize-ComparablePath ([string]$hotMount.Source)) -ne (Normalize-ComparablePath $source)) {
        throw "Active Hot source is not the frozen authoritative legacy root: $($hotMount.Source)"
    }
    if ((Normalize-ComparablePath ([string]$coldMount.Source)) -ne (Normalize-ComparablePath $coldExpected)) { throw 'Cold mount source drifted.' }
    if ((Normalize-ComparablePath ([string]$logMount.Source)) -ne (Normalize-ComparablePath $logsExpected)) { throw 'Log mount source drifted.' }

    $labelsJson = ((Invoke-DockerText -Arguments @('inspect','--format','{{json .Config.Labels}}',$cid)) -join '').Trim()
    $labels = $labelsJson | ConvertFrom-Json
    $composeConfigFiles = [string]$labels.'com.docker.compose.project.config_files'
    $composeWorkingDir = [string]$labels.'com.docker.compose.project.working_dir'
    if ($composeConfigFiles -notmatch 'docker-compose\.hot-cold-storage\.yml') { throw 'Running ClickHouse was not created with the Hot/Cold override.' }

    Write-Host 'preflight_stage=case_sensitivity_contract'
    $sourceCaseChain = @(Get-CaseChain $source)
    $caseChainComplete = @($sourceCaseChain | Where-Object { -not $_.exists }).Count -eq 0
    $caseChainAllDisabled = $caseChainComplete -and (@($sourceCaseChain | Where-Object { $_.case_sensitive -ne $false }).Count -eq 0)
    if (-not $caseChainAllDisabled) { throw 'Frozen source case-sensitivity evidence changed; refusing to prepare a different recovery scenario.' }

    Write-Host 'preflight_stage=logical_baseline'
    $schemaScript = @'
set -eu
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL"
'@
    $schemaLines = @(Invoke-ComposeScript 'clickhouse' $schemaScript | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    $schemaSnapshot = $schemaLines[-1]
    if ($schemaSnapshot -ne $ExpectedSchemaSnapshot) { throw "schema_version snapshot drifted: $schemaSnapshot" }

    $baselineScript = @'
set -eu
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT countDistinct(tuple(database, table)), count(), coalesce(sum(rows), 0), coalesce(sum(bytes_on_disk), 0) FROM system.parts WHERE active AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')"
'@
    $baselineLine = @(Invoke-ComposeScript 'clickhouse' $baselineScript | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })[-1]
    $baselineParts = $baselineLine -split "`t"
    if ($baselineParts.Count -ne 4) { throw "Unexpected active-parts baseline: $baselineLine" }

    $tableRowsScript = @'
set -eu
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT database, table, sum(rows) FROM system.parts WHERE active AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') GROUP BY database, table ORDER BY database, table"
'@
    $tableRows = @(Invoke-ComposeScript 'clickhouse' $tableRowsScript | Where-Object { $_.Trim() -ne '' })
    $tableRowsDigest = Get-TextSha256 $tableRows

    $tableUuidScript = @'
set -eu
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT database, name, toString(uuid) FROM system.tables WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') ORDER BY database, name"
'@
    $tableUuids = @(Invoke-ComposeScript 'clickhouse' $tableUuidScript | Where-Object { $_.Trim() -ne '' })
    $tableUuidDigest = Get-TextSha256 $tableUuids

    Write-Host 'preflight_stage=source_size_headroom'
    $sourceStatsScript = @'
set -eu
regular="$(find /source -type f -printf '%s\n' | awk '{bytes += $1; count += 1} END {printf "%.0f\t%.0f", bytes, count}')"
symlinks="$(find /source -type l -printf '.\n' | wc -l | tr -d ' ')"
dirs="$(find /source -mindepth 1 -type d -printf '.\n' | wc -l | tr -d ' ')"
printf '%s\t%s\t%s\n' "$regular" "$symlinks" "$dirs"
'@
    $statsLine = @(Invoke-DockerRunScript @('--rm','--user','0:0','--mount',"type=bind,source=$source,target=/source,readonly") $image $sourceStatsScript |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })[-1]
    $stats = $statsLine -split "`t"
    if ($stats.Count -ne 4) { throw "Unexpected source structural stats: $statsLine" }
    $sourceRegularBytes = [int64]$stats[0]
    $sourceRegularFileCount = [int64]$stats[1]
    $sourceSymlinkCount = [int64]$stats[2]
    $sourceDirectoryCount = [int64]$stats[3]

    $driveRoot = [System.IO.Path]::GetPathRoot($source)
    $drive = [System.IO.DriveInfo]::new($driveRoot)
    $freeBytes = [int64]$drive.AvailableFreeSpace
    $totalBytes = [int64]$drive.TotalSize
    $reserveBytes = [int64]$ReserveGiB * 1GB
    $requiredFreeBytes = $sourceRegularBytes + $reserveBytes
    $headroomOk = $freeBytes -ge $requiredFreeBytes

    $localEnv = Get-LocalEnvHotPath
    $processHot = [Environment]::GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'Process')
    $userHot = [Environment]::GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'User')
    $machineHot = [Environment]::GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'Machine')

    $runningServices = @(Invoke-DockerText -Arguments @('compose','ps','--services','--status','running') |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })

    $go = $headroomOk -and $caseChainAllDisabled -and -not (Test-Path -LiteralPath $destination)
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "clickhouse_authoritative_hot_recovery_preflight_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $reportPath = Join-Path $evidenceDir 'preflight.json'
    $report = [ordered]@{
        report_version = 'CLICKHOUSE_AUTHORITATIVE_HOT_RECOVERY_PREFLIGHT_V1'
        engine_sha = $head
        read_only_storage = $true
        recovery_preflight_go = [bool]$go
        source_hot_path = $source
        recovery_hot_path = $destination
        recovery_hot_path_exists = [bool](Test-Path -LiteralPath $destination)
        active_mount = [ordered]@{ source = [string]$hotMount.Source; type = [string]$hotMount.Type; rw = [bool]$hotMount.RW }
        cold_mount = [ordered]@{ source = [string]$coldMount.Source; type = [string]$coldMount.Type; rw = [bool]$coldMount.RW }
        log_mount = [ordered]@{ source = [string]$logMount.Source; type = [string]$logMount.Type; rw = [bool]$logMount.RW }
        compose = [ordered]@{ config_files = $composeConfigFiles; working_dir = $composeWorkingDir }
        environment_provenance = [ordered]@{
            local_env_exists = $localEnv.exists
            local_env_match_count = $localEnv.match_count
            local_env_value = $localEnv.value
            process_value = $processHot
            user_value = $userHot
            machine_value = $machineHot
        }
        clickhouse_image = $image
        clickhouse_health = $health
        schema_version_snapshot = $schemaSnapshot
        source_case_chain = $sourceCaseChain
        source_case_chain_all_disabled = [bool]$caseChainAllDisabled
        logical_baseline = [ordered]@{
            active_table_count = [int64]$baselineParts[0]
            active_part_count = [int64]$baselineParts[1]
            active_rows = [int64]$baselineParts[2]
            active_bytes_on_disk = [int64]$baselineParts[3]
            table_rows_count = $tableRows.Count
            table_rows_sha256 = $tableRowsDigest
            table_uuid_count = $tableUuids.Count
            table_uuid_sha256 = $tableUuidDigest
        }
        source_structure_observation = [ordered]@{
            regular_file_bytes = $sourceRegularBytes
            regular_file_count = $sourceRegularFileCount
            symlink_count = $sourceSymlinkCount
            directory_count = $sourceDirectoryCount
            measured_while_clickhouse_running = $true
            frozen_manifest = $false
        }
        headroom = [ordered]@{
            drive_root = $driveRoot
            free_bytes = $freeBytes
            total_bytes = $totalBytes
            reserve_bytes = $reserveBytes
            required_free_bytes = $requiredFreeBytes
            headroom_ok = [bool]$headroomOk
        }
        running_services = $runningServices
        worker_container_count_all_states = $workerAll.Count
        clickhouse_stop_performed = $false
        clickhouse_restart_performed = $false
        source_mutation_performed = $false
        destination_created = $false
        case_sensitivity_changed = $false
        copy_performed = $false
        schema_apply_performed = $false
        corpus_replay_performed = $false
        worker_start_performed = $false
        next_action = if ($go) { 'Build/execute stopped-ClickHouse case-sensitive recovery copy operator.' } else { 'Do not mutate storage; resolve preflight blocker first.' }
    }
    $report | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 $reportPath

    Write-Host '===== AUTHORITATIVE HOT RECOVERY PREFLIGHT RESULT ====='
    Write-Host "actual_hot_mount_source=$([string]$hotMount.Source)"
    Write-Host "actual_hot_mount_type=$([string]$hotMount.Type)"
    Write-Host "actual_hot_mount_rw=$([bool]$hotMount.RW)"
    Write-Host "clickhouse_image=$image"
    Write-Host "clickhouse_health=$health"
    Write-Host "compose_project_config_files=$composeConfigFiles"
    Write-Host "local_env_hot_path_match_count=$($localEnv.match_count)"
    Write-Host "local_env_hot_path=$($localEnv.value)"
    Write-Host "process_env_hot_path=$processHot"
    Write-Host "user_env_hot_path=$userHot"
    Write-Host "machine_env_hot_path=$machineHot"
    foreach ($item in $sourceCaseChain) {
        Write-Host ("source_case_chain|label={0}|exists={1}|case_sensitive={2}|path={3}" -f $item.label, $item.exists, $item.case_sensitive, $item.path)
    }
    Write-Host "schema_version_snapshot=$schemaSnapshot"
    Write-Host "active_table_count=$($baselineParts[0])"
    Write-Host "active_part_count=$($baselineParts[1])"
    Write-Host "active_rows=$($baselineParts[2])"
    Write-Host "active_bytes_on_disk=$($baselineParts[3])"
    Write-Host "table_rows_count=$($tableRows.Count)"
    Write-Host "table_rows_sha256=$tableRowsDigest"
    Write-Host "table_uuid_count=$($tableUuids.Count)"
    Write-Host "table_uuid_sha256=$tableUuidDigest"
    Write-Host "source_regular_file_bytes=$sourceRegularBytes"
    Write-Host "source_regular_file_count=$sourceRegularFileCount"
    Write-Host "source_symlink_count=$sourceSymlinkCount"
    Write-Host "source_directory_count=$sourceDirectoryCount"
    Write-Host "hot_drive_free_bytes=$freeBytes"
    Write-Host "recovery_required_free_bytes=$requiredFreeBytes"
    Write-Host "recovery_reserve_bytes=$reserveBytes"
    Write-Host "recovery_headroom_ok=$headroomOk"
    Write-Host "recovery_destination_exists=$(Test-Path -LiteralPath $destination)"
    Write-Host "recovery_preflight_go=$go"
    Write-Host 'clickhouse_stop_performed=False'
    Write-Host 'source_mutation_performed=False'
    Write-Host 'destination_created=False'
    Write-Host 'case_sensitivity_changed=False'
    Write-Host 'copy_performed=False'
    Write-Host 'schema_apply_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Report: $reportPath"
    if ($go) {
        Write-Host 'AUTHORITATIVE_HOT_RECOVERY_PREFLIGHT_GO'
    }
    else {
        Write-Host 'AUTHORITATIVE_HOT_RECOVERY_PREFLIGHT_BLOCKED'
        throw 'Recovery preflight is blocked. No storage mutation was performed.'
    }
}
catch {
    Write-Host 'AUTHORITATIVE_HOT_RECOVERY_PREFLIGHT_FAILURE'
    Write-Host "exception_type=$($_.Exception.GetType().FullName)"
    Write-Host "exception_message=$($_.Exception.Message)"
    Write-Host "script_stack_trace=$($_.ScriptStackTrace)"
    throw
}
finally {
    Pop-Location
}
