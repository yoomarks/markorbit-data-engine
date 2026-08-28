param(
    [string]$OldHotPath = "E:\MarkOrbitData\hot\clickhouse-cs",
    [string]$NewHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$ColdPath = "F:\MarkOrbitData\cold\clickhouse",
    [string]$LogPath = "E:\MarkOrbitData\hot\clickhouse-logs",
    [string]$PreProfilePath = "reports\cn_hot_warm_capacity_pre_hot_rename_20260828_095611.json"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return "" }
    return (($Path -replace '/', '\\').TrimEnd('\')).ToLowerInvariant()
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

function Assert-FsutilCaseSensitiveEnabled([string]$Path) {
    $lines = @(& fsutil.exe file queryCaseSensitiveInfo $Path 2>&1)
    $exitCode = $LASTEXITCODE
    $text = ($lines -join "`n").Trim()
    if ($exitCode -ne 0) {
        throw "fsutil could not query case sensitivity for $Path (exit=$exitCode): $text"
    }
    Write-Host $text
    if ($text -match '(?i)\bis enabled\b' -or $text -match '已启用' -or $text -match '已啟用') {
        return
    }
    if ($text -match '(?i)\bis disabled\b' -or $text -match '已禁用' -or $text -match '已停用' -or $text -match '未启用' -or $text -match '未啟用') {
        throw "Directory is not case-sensitive: $Path"
    }
    throw "Could not positively identify the fsutil case-sensitivity state for $Path."
}

try {
    Write-Host "`n===== POST-RENAME RECOVERY PREFLIGHT ====="

    if (-not (Test-IsAdministrator)) {
        throw "Run this recovery operator from an elevated Administrator PowerShell."
    }
    Write-Host "ADMINISTRATOR_OK"

    docker info | Out-Null
    Assert-LastExitCode "Docker Engine is unavailable."

    if (Test-Path -LiteralPath $OldHotPath) {
        throw "Old Hot path still exists. This recovery operator only accepts the already-renamed state."
    }
    if (-not (Test-Path -LiteralPath $NewHotPath -PathType Container)) {
        throw "Renamed Hot directory missing: $NewHotPath"
    }
    if (-not (Test-Path -LiteralPath $ColdPath -PathType Container)) {
        throw "Cold directory missing: $ColdPath"
    }
    if (-not (Test-Path -LiteralPath $LogPath -PathType Container)) {
        throw "ClickHouse log directory missing: $LogPath"
    }

    Assert-FsutilCaseSensitiveEnabled $NewHotPath
    Write-Host "NEW_HOT_CASE_SENSITIVE_OK"

    $runningNames = @(docker ps --format "{{.Names}}")
    if ($runningNames -contains "markorbit-data-engine-worker-1") {
        throw "Persistent worker is running. Recovery remains blocked."
    }
    if ($runningNames -contains "markorbit-data-engine-api-1") {
        throw "API container is running. Recovery remains blocked."
    }

    $existingNames = @(docker ps -a --format "{{.Names}}")
    if ($existingNames -contains "markorbit-data-engine-clickhouse-1") {
        throw "ClickHouse container already exists. Stop and audit instead of recreating it."
    }

    docker volume inspect markorbit-data-engine_postgres_data | Out-Null
    Assert-LastExitCode "PostgreSQL persistent volume is missing."

    if (-not (Test-Path -LiteralPath $PreProfilePath -PathType Leaf)) {
        throw "Pre-rename capacity profile missing: $PreProfilePath"
    }

    $pre = Get-Content -LiteralPath $PreProfilePath -Raw | ConvertFrom-Json
    Write-Host "Pre-rename active rows: $($pre.active_totals.rows_from_parts)"

    Write-Host "`n===== RESOLVE NEW HOT/COLD COMPOSE MODEL ====="

    $env:CLICKHOUSE_HOT_DATA_PATH = $NewHotPath.Replace('\', '/')
    $env:CLICKHOUSE_COLD_DATA_PATH = $ColdPath.Replace('\', '/')
    $env:CLICKHOUSE_LOG_PATH = $LogPath.Replace('\', '/')

    $compose = @(
        "-f", "docker-compose.yml",
        "-f", "docker-compose.hot-cold-storage.yml"
    )

    $rawConfig = docker compose @compose config --format json
    Assert-LastExitCode "Unable to resolve Hot/Cold compose model."
    $cfg = ($rawConfig | Out-String) | ConvertFrom-Json

    $chVolumes = @($cfg.services.clickhouse.volumes)
    $resolvedHot = $chVolumes | Where-Object { $_.target -eq "/var/lib/clickhouse" }
    $resolvedCold = $chVolumes | Where-Object { $_.target -eq "/var/lib/clickhouse-cold" }
    $resolvedLogs = $chVolumes | Where-Object { $_.target -eq "/var/log/clickhouse-server" }

    Write-Host "Resolved Hot  : $($resolvedHot.source)"
    Write-Host "Resolved Cold : $($resolvedCold.source)"
    Write-Host "Resolved Logs : $($resolvedLogs.source)"

    if ((Normalize-HostPath $resolvedHot.source) -ne (Normalize-HostPath $NewHotPath)) {
        throw "Resolved Hot source is not the renamed directory."
    }
    if ((Normalize-HostPath $resolvedCold.source) -ne (Normalize-HostPath $ColdPath)) {
        throw "Resolved Cold source is unexpected."
    }
    if ((Normalize-HostPath $resolvedLogs.source) -ne (Normalize-HostPath $LogPath)) {
        throw "Resolved Logs source is unexpected."
    }
    if ($null -ne $cfg.services.clickhouse.depends_on) {
        throw "ClickHouse unexpectedly has compose dependencies. Single-service shell creation is blocked."
    }

    Write-Host "COMPOSE_MODEL_OK"
    Write-Host "CLICKHOUSE_HAS_NO_DEPENDENCIES_OK"

    Write-Host "`n===== CREATE CLICKHOUSE CONTAINER SHELL ====="

    # `docker compose create` does not support --no-deps on the accepted
    # target-host Compose version. The resolved ClickHouse service has no
    # depends_on entries, so naming only `clickhouse` remains single-service.
    docker compose @compose create clickhouse
    Assert-LastExitCode "Failed to create ClickHouse container shell."

    $cid = docker compose @compose ps -a -q clickhouse
    if ([string]::IsNullOrWhiteSpace($cid)) {
        throw "Created ClickHouse container shell not found."
    }

    $created = (docker inspect $cid | ConvertFrom-Json)[0]
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

    Write-Host "CREATED_MOUNTS_OK"

    Write-Host "`n===== START AND VERIFY CLICKHOUSE ====="

    docker start $cid | Out-Host
    Assert-LastExitCode "Failed starting ClickHouse."

    $deadline = (Get-Date).AddMinutes(5)
    do {
        $runtime = (docker inspect $cid | ConvertFrom-Json)[0]
        $health = if ($runtime.State.Health) { $runtime.State.Health.Status } else { "none" }
        Write-Host "ClickHouse status=$($runtime.State.Status) health=$health"
        if ($runtime.State.Status -eq "running" -and $health -eq "healthy") { break }
        if ($runtime.State.Status -in @("dead", "exited")) {
            docker logs --tail 200 $cid
            throw "ClickHouse exited during recovery."
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    if ($runtime.State.Status -ne "running" -or $health -ne "healthy") {
        docker logs --tail 200 $cid
        throw "ClickHouse did not become healthy."
    }

    Write-Host "CLICKHOUSE_HEALTHY"

    Write-Host "`n===== POST-RENAME CN EVIDENCE ====="

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $postServing = Join-Path "reports" "cn_serving_state_post_hot_rename_recovery_$stamp.json"
    $postProfile = Join-Path "reports" "cn_hot_warm_capacity_post_hot_rename_recovery_$stamp.json"

    powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-serving-state.ps1 -Compact -OutputPath $postServing
    Assert-LastExitCode "CN serving-state failed after recovery."

    powershell.exe -ExecutionPolicy Bypass -File .\scripts\profile-cn-hot-warm-capacity.ps1 -OutputPath $postProfile
    Assert-LastExitCode "CN capacity profile failed after recovery."

    $post = Get-Content -LiteralPath $postProfile -Raw | ConvertFrom-Json

    if ([int64]$pre.active_totals.rows_from_parts -ne [int64]$post.active_totals.rows_from_parts) {
        throw "Total active rows changed across Hot-path rename recovery."
    }

    $preRows = @{}
    foreach ($table in @($pre.tables)) { $preRows[[string]$table.table] = [int64]$table.rows_from_parts }
    $postRows = @{}
    foreach ($table in @($post.tables)) { $postRows[[string]$table.table] = [int64]$table.rows_from_parts }

    if ($preRows.Count -ne $postRows.Count) {
        throw "Active table count changed across Hot-path rename recovery."
    }
    foreach ($name in $preRows.Keys) {
        if (-not $postRows.ContainsKey($name)) {
            throw "Active table missing after recovery: $name"
        }
        if ($preRows[$name] -ne $postRows[$name]) {
            throw "Active rows changed for table $name after recovery."
        }
    }

    Write-Host "ROW_EQUIVALENCE_OK"

    $runningNames = @(docker ps --format "{{.Names}}")
    if ($runningNames -contains "markorbit-data-engine-worker-1" -or $runningNames -contains "markorbit-data-engine-api-1") {
        throw "API/worker unexpectedly running after recovery."
    }

    Write-Host "API_WORKER_STOPPED_OK"
    Write-Host "Hot path     : $NewHotPath"
    Write-Host "Post serving : $postServing"
    Write-Host "Post profile : $postProfile"
    Write-Host "Active rows  : $($post.active_totals.rows_from_parts)"
    Write-Host "POST_RENAME_RECOVERY_OK"
}
finally {
    Pop-Location
}
