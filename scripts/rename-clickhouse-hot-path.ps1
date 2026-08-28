param(
    [string]$OldHotPath = "E:\MarkOrbitData\hot\clickhouse-cs",
    [string]$NewHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$ColdPath = "F:\MarkOrbitData\cold\clickhouse",
    [string]$LogPath = "E:\MarkOrbitData\hot\clickhouse-logs"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return "" }
    return (($Path -replace '/', '\').TrimEnd('\')).ToLowerInvariant()
}

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
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

function Test-CaseSensitiveDirectory([string]$Path) {
    $info = New-Object MarkOrbit.NativeCaseSensitivity+FILE_CASE_SENSITIVE_INFORMATION
    $ok = [MarkOrbit.NativeCaseSensitivity]::GetFileInformationByName(
        $Path,
        2,
        [ref]$info,
        4
    )
    if (-not $ok) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw "Unable to query NTFS case-sensitivity for $Path (Win32 error $errorCode)."
    }
    return (($info.Flags -band 0x00000001) -eq 0x00000001)
}

try {
    Write-Host "`n===== HOT PATH RENAME PREFLIGHT ====="

    docker info | Out-Null
    Assert-LastExitCode "Docker Engine is unavailable."

    if (-not (Test-Path -LiteralPath $OldHotPath -PathType Container)) {
        throw "Old Hot directory missing: $OldHotPath"
    }
    if (Test-Path -LiteralPath $NewHotPath) {
        throw "New Hot path already exists: $NewHotPath"
    }
    if (-not (Test-Path -LiteralPath $ColdPath -PathType Container)) {
        throw "Cold directory missing: $ColdPath"
    }
    if (-not (Test-Path -LiteralPath $LogPath -PathType Container)) {
        throw "ClickHouse log directory missing: $LogPath"
    }

    $oldParent = Normalize-HostPath (Split-Path -Parent $OldHotPath)
    $newParent = Normalize-HostPath (Split-Path -Parent $NewHotPath)
    if ($oldParent -ne $newParent) {
        throw "Old/New Hot paths must share the same parent. This operator only permits same-directory rename, never a data copy/move."
    }

    if (-not (Test-CaseSensitiveDirectory $OldHotPath)) {
        throw "Old Hot directory is not case-sensitive. Nothing changed."
    }
    Write-Host "OLD_HOT_CASE_SENSITIVE_OK"

    $runningNames = @(docker ps --format "{{.Names}}")
    if ($runningNames -contains "markorbit-data-engine-worker-1") {
        throw "Persistent worker is running. Stop it before Hot path rename."
    }
    if ($runningNames -contains "markorbit-data-engine-api-1") {
        throw "API container is running. Stop it before Hot path rename."
    }

    $compose = @(
        "-f", "docker-compose.yml",
        "-f", "docker-compose.hot-cold-storage.yml"
    )

    $cid = docker compose ps -q clickhouse
    if ([string]::IsNullOrWhiteSpace($cid)) {
        throw "Current ClickHouse container not found."
    }

    $current = (docker inspect $cid | ConvertFrom-Json)[0]
    $dataMount = $current.Mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse" }
    $coldMount = $current.Mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse-cold" }
    $logMount = $current.Mounts | Where-Object { $_.Destination -eq "/var/log/clickhouse-server" }

    Write-Host "Current status : $($current.State.Status)"
    Write-Host "Current health : $($current.State.Health.Status)"
    Write-Host "Current Hot    : $($dataMount.Source)"
    Write-Host "Current Cold   : $($coldMount.Source)"
    Write-Host "Current Logs   : $($logMount.Source)"

    if ($current.State.Status -ne "running" -or $current.State.Health.Status -ne "healthy") {
        throw "ClickHouse is not healthy before rename."
    }
    if ((Normalize-HostPath $dataMount.Source) -ne (Normalize-HostPath $OldHotPath)) {
        throw "Current ClickHouse Hot mount is not the expected old path."
    }
    if ((Normalize-HostPath $coldMount.Source) -ne (Normalize-HostPath $ColdPath)) {
        throw "Current ClickHouse Cold mount is unexpected."
    }
    if ((Normalize-HostPath $logMount.Source) -ne (Normalize-HostPath $LogPath)) {
        throw "Current ClickHouse log mount is unexpected."
    }

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $preProfile = Join-Path "reports" "cn_hot_warm_capacity_pre_hot_rename_$stamp.json"
    $postProfile = Join-Path "reports" "cn_hot_warm_capacity_post_hot_rename_$stamp.json"
    $preServing = Join-Path "reports" "cn_serving_state_pre_hot_rename_$stamp.json"
    $postServing = Join-Path "reports" "cn_serving_state_post_hot_rename_$stamp.json"

    Write-Host "`n===== PRE-RENAME READ-ONLY EVIDENCE ====="

    powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-serving-state.ps1 -Compact -OutputPath $preServing
    Assert-LastExitCode "CN serving-state is not PASS before rename."

    powershell.exe -ExecutionPolicy Bypass -File .\scripts\profile-cn-hot-warm-capacity.ps1 -OutputPath $preProfile
    Assert-LastExitCode "CN capacity profile failed before rename."

    $pre = Get-Content -LiteralPath $preProfile -Raw | ConvertFrom-Json

    Write-Host "`n===== STOP CLICKHOUSE ONLY ====="

    docker stop --time 60 $cid | Out-Host
    Assert-LastExitCode "Failed to stop ClickHouse."

    $stopped = (docker inspect $cid | ConvertFrom-Json)[0]
    if ($stopped.State.Status -ne "exited") {
        throw "ClickHouse did not stop cleanly."
    }

    Write-Host "`n===== SAME-VOLUME DIRECTORY RENAME ====="

    Rename-Item -LiteralPath $OldHotPath -NewName (Split-Path -Leaf $NewHotPath)

    if (Test-Path -LiteralPath $OldHotPath) {
        throw "Old Hot path still exists after rename."
    }
    if (-not (Test-Path -LiteralPath $NewHotPath -PathType Container)) {
        throw "New Hot path missing after rename."
    }

    if (-not (Test-CaseSensitiveDirectory $NewHotPath)) {
        Write-Host "CASE-SENSITIVE FLAG WAS NOT PRESERVED. ROLLING PATH NAME BACK."
        Rename-Item -LiteralPath $NewHotPath -NewName (Split-Path -Leaf $OldHotPath)
        docker start $cid | Out-Host
        throw "Case-sensitive flag not preserved after rename. Path was rolled back; old container start was attempted."
    }
    Write-Host "NEW_HOT_CASE_SENSITIVE_OK"

    Write-Host "`n===== RECREATE CLICKHOUSE CONTAINER SHELL ====="

    docker rm $cid | Out-Host
    Assert-LastExitCode "Failed removing stopped ClickHouse container shell. Data directory was not touched."

    $env:CLICKHOUSE_HOT_DATA_PATH = ($NewHotPath -replace '\\', '/')
    $env:CLICKHOUSE_COLD_DATA_PATH = ($ColdPath -replace '\\', '/')
    $env:CLICKHOUSE_LOG_PATH = ($LogPath -replace '\\', '/')

    $rawConfig = docker compose @compose config --format json
    Assert-LastExitCode "Unable to resolve Hot/Cold compose model for new path."
    $cfg = ($rawConfig | Out-String) | ConvertFrom-Json
    $resolvedHot = @($cfg.services.clickhouse.volumes) | Where-Object { $_.target -eq "/var/lib/clickhouse" }
    if ((Normalize-HostPath $resolvedHot.source) -ne (Normalize-HostPath $NewHotPath)) {
        throw "Resolved compose Hot source is not the renamed directory."
    }

    docker compose @compose create --no-deps clickhouse
    Assert-LastExitCode "Failed to create ClickHouse container shell for renamed Hot path."

    $newCid = docker compose @compose ps -a -q clickhouse
    if ([string]::IsNullOrWhiteSpace($newCid)) {
        throw "New ClickHouse container shell not found."
    }

    $created = (docker inspect $newCid | ConvertFrom-Json)[0]
    $createdHot = $created.Mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse" }
    $createdCold = $created.Mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse-cold" }
    $createdLogs = $created.Mounts | Where-Object { $_.Destination -eq "/var/log/clickhouse-server" }

    Write-Host "Created Hot  : $($createdHot.Source)"
    Write-Host "Created Cold : $($createdCold.Source)"
    Write-Host "Created Logs : $($createdLogs.Source)"

    if ($createdHot.Type -ne "bind" -or (Normalize-HostPath $createdHot.Source) -ne (Normalize-HostPath $NewHotPath)) {
        throw "Created ClickHouse container has wrong Hot mount. DO NOT START."
    }
    if ($createdCold.Type -ne "bind" -or (Normalize-HostPath $createdCold.Source) -ne (Normalize-HostPath $ColdPath)) {
        throw "Created ClickHouse container has wrong Cold mount. DO NOT START."
    }
    if ($createdLogs.Type -ne "bind" -or (Normalize-HostPath $createdLogs.Source) -ne (Normalize-HostPath $LogPath)) {
        throw "Created ClickHouse container has wrong Logs mount. DO NOT START."
    }

    Write-Host "`n===== START AND VERIFY CLICKHOUSE ====="

    docker start $newCid | Out-Host
    Assert-LastExitCode "Failed starting ClickHouse after Hot path rename."

    $deadline = (Get-Date).AddMinutes(5)
    do {
        $runtime = (docker inspect $newCid | ConvertFrom-Json)[0]
        $health = if ($runtime.State.Health) { $runtime.State.Health.Status } else { "none" }
        Write-Host "ClickHouse status=$($runtime.State.Status) health=$health"
        if ($runtime.State.Status -eq "running" -and $health -eq "healthy") { break }
        if ($runtime.State.Status -in @("dead", "exited")) {
            docker logs --tail 200 $newCid
            throw "ClickHouse exited after Hot path rename."
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    if ($runtime.State.Status -ne "running" -or $health -ne "healthy") {
        docker logs --tail 200 $newCid
        throw "ClickHouse did not become healthy after Hot path rename."
    }

    Write-Host "`n===== POST-RENAME READ-ONLY EVIDENCE ====="

    powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-serving-state.ps1 -Compact -OutputPath $postServing
    Assert-LastExitCode "CN serving-state failed after Hot path rename."

    powershell.exe -ExecutionPolicy Bypass -File .\scripts\profile-cn-hot-warm-capacity.ps1 -OutputPath $postProfile
    Assert-LastExitCode "CN capacity profile failed after Hot path rename."

    $post = Get-Content -LiteralPath $postProfile -Raw | ConvertFrom-Json

    if ([int64]$pre.active_totals.rows_from_parts -ne [int64]$post.active_totals.rows_from_parts) {
        throw "Total active rows changed across path-only rename."
    }

    $preRows = @{}
    foreach ($table in @($pre.tables)) { $preRows[[string]$table.table] = [int64]$table.rows_from_parts }
    $postRows = @{}
    foreach ($table in @($post.tables)) { $postRows[[string]$table.table] = [int64]$table.rows_from_parts }

    if ($preRows.Count -ne $postRows.Count) {
        throw "Active table count changed across path-only rename."
    }
    foreach ($name in $preRows.Keys) {
        if (-not $postRows.ContainsKey($name)) {
            throw "Active table missing after rename: $name"
        }
        if ($preRows[$name] -ne $postRows[$name]) {
            throw "Active rows changed for table $name across path-only rename."
        }
    }

    Write-Host "`n===== FINAL STATE ====="
    Write-Host "Hot path      : $NewHotPath"
    Write-Host "Cold path     : $ColdPath"
    Write-Host "Log path      : $LogPath"
    Write-Host "Pre profile   : $preProfile"
    Write-Host "Post profile  : $postProfile"
    Write-Host "Pre serving   : $preServing"
    Write-Host "Post serving  : $postServing"
    Write-Host "Active rows   : $($post.active_totals.rows_from_parts)"
    Write-Host "HOT_PATH_RENAME_OK"
}
finally {
    Pop-Location
}
