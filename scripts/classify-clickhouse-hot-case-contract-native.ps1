param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$ExpectedHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$ExpectedColdPath = "F:\MarkOrbitData\cold\clickhouse",
    [string]$ExpectedLogPath = "E:\MarkOrbitData\hot\clickhouse-logs",
    [string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545"
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

if (-not ("MarkOrbit.NativeCaseSensitivity" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace MarkOrbit {
    public static class NativeCaseSensitivity {
        [StructLayout(LayoutKind.Sequential)]
        public struct FILE_CASE_SENSITIVE_INFORMATION {
            public UInt32 Flags;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool GetFileInformationByName(
            string FileName,
            int FileInformationClass,
            out FILE_CASE_SENSITIVE_INFORMATION FileInfoBuffer,
            UInt32 FileInfoBufferSize
        );
    }
}
'@
}

function Get-NativeCaseSensitivity([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return [ordered]@{ exists = $false; enabled = $null; win32_error = $null }
    }
    $info = New-Object MarkOrbit.NativeCaseSensitivity+FILE_CASE_SENSITIVE_INFORMATION
    $ok = [MarkOrbit.NativeCaseSensitivity]::GetFileInformationByName(
        $Path,
        2,
        [ref]$info,
        4
    )
    if (-not $ok) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "Unable to query native case-sensitive flag for $Path (Win32 error $errorCode)."
    }
    return [ordered]@{
        exists = $true
        enabled = [bool](($info.Flags -band 0x00000001) -eq 0x00000001)
        win32_error = 0
    }
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
        $state = Get-NativeCaseSensitivity $item.path
        $rows += [pscustomobject][ordered]@{
            label = $item.label
            path = $item.path
            exists = [bool]$state.exists
            case_sensitive = $state.enabled
            win32_error = $state.win32_error
        }
    }
    return $rows
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
    Write-Host '===== NATIVE HOT CASE-CONTRACT CLASSIFICATION ====='
    if (git status --porcelain) { throw 'Working tree must be clean.' }
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Classifier must run from main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed.' }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw 'Exact-main mismatch.' }

    Write-Host 'classifier_stage=global_idle_zero_worker'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop-idle-worker.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Global idle gate failed.' }
    $workerAll = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($workerAll.Count -ne 0) { throw 'Worker containers must be absent.' }
    Write-Host "worker_container_count_all_states=$($workerAll.Count)"

    $hotExpected = Normalize-WindowsPath $ExpectedHotPath 'ExpectedHotPath'
    $coldExpected = Normalize-WindowsPath $ExpectedColdPath 'ExpectedColdPath'
    $logsExpected = Normalize-WindowsPath $ExpectedLogPath 'ExpectedLogPath'

    Write-Host 'classifier_stage=inspect_runtime_mounts'
    $clickhouseIds = @(Invoke-DockerText -Arguments @('compose','ps','--status','running','-q','clickhouse') |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($clickhouseIds.Count -ne 1) { throw 'Exactly one running ClickHouse container is required.' }
    $cid = $clickhouseIds[0]
    $health = ((Invoke-DockerText -Arguments @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$cid)) -join '').Trim()
    if ($health -ne 'healthy') { throw "ClickHouse must be healthy; observed=$health" }
    $image = ((Invoke-DockerText -Arguments @('inspect','--format','{{.Config.Image}}',$cid)) -join '').Trim()
    if ($image -notmatch ':24\.8(?:$|[.-])') { throw "Unexpected ClickHouse image: $image" }
    $mountsJson = ((Invoke-DockerText -Arguments @('inspect','--format','{{json .Mounts}}',$cid)) -join '').Trim()
    $mounts = $mountsJson | ConvertFrom-Json
    $hotMount = Get-SingleMountByDestination $mounts '/var/lib/clickhouse'
    $coldMount = Get-SingleMountByDestination $mounts '/var/lib/clickhouse-cold'
    $logMount = Get-SingleMountByDestination $mounts '/var/log/clickhouse-server'
    if ([string]$hotMount.Type -ne 'bind' -or -not [bool]$hotMount.RW) { throw 'Active Hot mount must be a read/write bind.' }
    if ((Normalize-ComparablePath ([string]$hotMount.Source)) -ne (Normalize-ComparablePath $hotExpected)) { throw "Hot mount source drifted: $($hotMount.Source)" }
    if ((Normalize-ComparablePath ([string]$coldMount.Source)) -ne (Normalize-ComparablePath $coldExpected)) { throw 'Cold mount source drifted.' }
    if ((Normalize-ComparablePath ([string]$logMount.Source)) -ne (Normalize-ComparablePath $logsExpected)) { throw 'Log mount source drifted.' }

    Write-Host 'classifier_stage=native_case_chain'
    $chain = @(Get-CaseChain $hotExpected)
    $complete = @($chain | Where-Object { -not $_.exists }).Count -eq 0
    $allEnabled = $complete -and (@($chain | Where-Object { $_.case_sensitive -ne $true }).Count -eq 0)
    $allDisabled = $complete -and (@($chain | Where-Object { $_.case_sensitive -ne $false }).Count -eq 0)
    $classification = if (-not $complete) { 'INCOMPLETE' } elseif ($allEnabled) { 'ALL_ENABLED' } elseif ($allDisabled) { 'ALL_DISABLED' } else { 'MIXED' }

    foreach ($item in $chain) {
        Write-Host ("native_case_chain|label={0}|exists={1}|case_sensitive={2}|win32_error={3}|path={4}" -f $item.label, $item.exists, $item.case_sensitive, $item.win32_error, $item.path)
    }
    Write-Host "case_contract_classification=$classification"
    Write-Host "case_recovery_required=$([bool]($classification -eq 'ALL_DISABLED'))"

    Write-Host 'classifier_stage=verify_schema_snapshot'
    $snapshot = (& docker compose exec -T clickhouse clickhouse-client --query "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL").Trim()
    if ($LASTEXITCODE -ne 0 -or $snapshot -ne $ExpectedSchemaSnapshot) { throw "schema_version snapshot drifted: $snapshot" }
    Write-Host "schema_version_snapshot=$snapshot"

    $localEnv = Get-LocalEnvHotPath
    $processHot = [Environment]::GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'Process')
    $userHot = [Environment]::GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'User')
    $machineHot = [Environment]::GetEnvironmentVariable('CLICKHOUSE_HOT_DATA_PATH', 'Machine')
    Write-Host "local_env_hot_path_match_count=$($localEnv.match_count)"
    Write-Host "local_env_hot_path=$($localEnv.value)"
    Write-Host "process_env_hot_path=$processHot"
    Write-Host "user_env_hot_path=$userHot"
    Write-Host "machine_env_hot_path=$machineHot"
    Write-Host "actual_hot_mount_source=$([string]$hotMount.Source)"
    Write-Host "clickhouse_image=$image"
    Write-Host "clickhouse_health=$health"
    Write-Host 'storage_mutation_performed=False'
    Write-Host 'clickhouse_stop_performed=False'
    Write-Host 'schema_apply_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host 'NATIVE_HOT_CASE_CONTRACT_CLASSIFICATION_COMPLETE'
}
catch {
    Write-Host 'NATIVE_HOT_CASE_CONTRACT_CLASSIFICATION_FAILURE'
    Write-Host "exception_type=$($_.Exception.GetType().FullName)"
    Write-Host "exception_message=$($_.Exception.Message)"
    Write-Host "script_stack_trace=$($_.ScriptStackTrace)"
    throw
}
finally {
    Pop-Location
}
