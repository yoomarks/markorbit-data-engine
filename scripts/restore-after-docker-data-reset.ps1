param(
    [string]$RecoveryRoot = "F:\MarkOrbitData\recovery\docker-data-reset",
    [string]$HotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$ColdPath = "F:\MarkOrbitData\cold\clickhouse",
    [string]$LogPath = "E:\MarkOrbitData\hot\clickhouse-logs",
    [string]$ExpectedDockerDataRoot = "D:\DockerData\DockerDesktopWSL"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

function Assert-FileReceipt(
    [string]$Path,
    [int64]$ExpectedBytes,
    [string]$ExpectedHash,
    [string]$ReceiptHash
) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required recovery file missing: $Path"
    }
    $item = Get-Item -LiteralPath $Path
    if ([int64]$item.Length -ne $ExpectedBytes) {
        throw "Recovery file byte length changed after preparation: $Path"
    }
    if ($ExpectedHash -notmatch '^[0-9a-fA-F]{64}$' -or $ReceiptHash -notmatch '^[0-9a-fA-F]{64}$') {
        throw "Recovery SHA256 receipt is malformed: $Path"
    }
    if ($ExpectedHash.ToLowerInvariant() -ne $ReceiptHash.ToLowerInvariant()) {
        throw "Recovery SHA256 receipt chain mismatch: $Path"
    }
}

function Assert-AncillaryReceipt($Entry) {
    $path = [string]$Entry.archive
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Ancillary recovery file missing: $path"
    }
    $item = Get-Item -LiteralPath $path
    if ([int64]$item.Length -ne [int64]$Entry.bytes) {
        throw "Ancillary recovery file byte length changed after preparation: $path"
    }
    if ([string]$Entry.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw "Ancillary SHA256 receipt is malformed: $path"
    }
}

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return "" }
    return $Path.Replace([char]47, [char]92).TrimEnd([char]92).ToLowerInvariant()
}

function Wait-Healthy([string]$Name, [int]$Minutes = 8) {
    $deadline = (Get-Date).AddMinutes($Minutes)
    do {
        $raw = docker inspect $Name 2>$null
        Assert-LastExitCode "Container missing while waiting for health: $Name"
        $obj = (($raw | Out-String) | ConvertFrom-Json)[0]
        $health = if ($obj.State.Health) { $obj.State.Health.Status } else { "none" }
        Write-Host "$Name status=$($obj.State.Status) health=$health"
        if ($obj.State.Status -eq "running" -and $health -eq "healthy") { return $obj }
        if ($obj.State.Status -in @("dead", "exited")) {
            docker logs --tail 150 $Name
            throw "$Name exited during restore."
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not become healthy within the allowed window."
}

try {
    Write-Host "`n===== DOCKER DATA RESET RESTORE PREFLIGHT ====="

    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run from an Administrator PowerShell session."
    }
    Write-Host "ADMINISTRATOR_OK"

    docker info | Out-Null
    Assert-LastExitCode "Docker Engine is unavailable after reset."
    Write-Host "DOCKER_ENGINE_OK"

    $prepDir = Get-ChildItem -LiteralPath $RecoveryRoot -Directory |
        Sort-Object LastWriteTime -Descending |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "manifest.json") } |
        Select-Object -First 1
    if ($null -eq $prepDir) { throw "No Docker reset preparation manifest found under $RecoveryRoot" }

    $prepManifestPath = Join-Path $prepDir.FullName "manifest.json"
    $prep = Get-Content -Raw -LiteralPath $prepManifestPath | ConvertFrom-Json
    if ($prep.schema_version -ne "DOCKER_DATA_RESET_PREPARATION_V1" -or [bool]$prep.destructive_action_performed) {
        throw "Unexpected or unsafe Docker reset preparation manifest."
    }
    Write-Host "PREPARATION_MANIFEST_OK"

    $pgManifest = Get-Content -Raw -LiteralPath $prep.postgres_backup_manifest | ConvertFrom-Json
    if ($pgManifest.schema_version -ne "POSTGRES_BEFORE_DOCKER_DATA_RESET_V1") {
        throw "Unexpected PostgreSQL backup manifest schema."
    }

    Assert-FileReceipt ([string]$pgManifest.logical_backup.path) ([int64]$pgManifest.logical_backup.bytes) ([string]$pgManifest.logical_backup.sha256) ([string]$prep.postgres_logical_sha256)
    Write-Host "LOGICAL_BACKUP_RECEIPT_OK"

    Assert-FileReceipt ([string]$pgManifest.cold_pgdata_backup.path) ([int64]$pgManifest.cold_pgdata_backup.bytes) ([string]$pgManifest.cold_pgdata_backup.sha256) ([string]$prep.postgres_pgdata_sha256)
    Write-Host "COLD_PGDATA_RECEIPT_OK"

    foreach ($entry in @($prep.ancillary_volumes)) {
        Assert-AncillaryReceipt $entry
        Write-Host "Ancillary receipt OK: $([string]$entry.volume)"
    }
    Write-Host "RECOVERY_RECEIPT_CHAIN_OK"

    foreach ($p in @($HotPath, $ColdPath, $LogPath)) {
        if (-not (Test-Path -LiteralPath $p -PathType Container)) { throw "Persistent bind path missing after reset: $p" }
    }
    fsutil.exe file queryCaseSensitiveInfo $HotPath | Out-Host
    Assert-LastExitCode "Unable to query Hot directory case-sensitive attribute."
    Write-Host "HOT_CASE_SENSITIVE_QUERY_OK"

    $dVhdx = Join-Path $ExpectedDockerDataRoot "disk\docker_data.vhdx"
    $defaultCVhdx = Join-Path $env:LOCALAPPDATA "Docker\wsl\data\docker_data.vhdx"
    if (-not (Test-Path -LiteralPath $dVhdx -PathType Leaf)) {
        if (Test-Path -LiteralPath $defaultCVhdx -PathType Leaf) {
            throw "Docker reset recreated its data disk on C:. Move Disk image location back to D: before restoring. Found: $defaultCVhdx"
        }
        throw "Expected fresh Docker data disk not found at $dVhdx"
    }
    $freshBytes = (Get-Item -LiteralPath $dVhdx).Length
    Write-Host ("Fresh Docker VHDX before restore: {0:N2} GiB" -f ($freshBytes / 1GB))
    if ($freshBytes -gt 250GB) { throw "Docker data disk remains unexpectedly large. Clean-up/reset is not accepted." }
    Write-Host "FRESH_DOCKER_DATA_DISK_OK"

    $existingNames = @(docker ps -a --format "{{.Names}}")
    foreach ($name in @("markorbit-data-engine-postgres-1", "markorbit-data-engine-clickhouse-1", "markorbit-data-engine-api-1", "markorbit-data-engine-worker-1")) {
        if ($existingNames -contains $name) { throw "Expected clean Docker state but container exists: $name" }
    }
    if (@(docker volume ls --format "{{.Name}}") -contains "markorbit-data-engine_postgres_data") {
        throw "Expected clean Docker state but PostgreSQL volume already exists."
    }
    Write-Host "CLEAN_DOCKER_STATE_OK"

    $env:CLICKHOUSE_HOT_DATA_PATH = $HotPath.Replace([char]92, [char]47)
    $env:CLICKHOUSE_COLD_DATA_PATH = $ColdPath.Replace([char]92, [char]47)
    $env:CLICKHOUSE_LOG_PATH = $LogPath.Replace([char]92, [char]47)
    $compose = @("-f", "docker-compose.yml", "-f", "docker-compose.hot-cold-storage.yml")

    Write-Host "`n===== RESOLVE COMPOSE AND PULL DATABASE IMAGES ====="

    $rawConfig = docker compose @compose config --format json
    Assert-LastExitCode "Unable to resolve Hot/Cold Compose configuration."
    $cfg = ($rawConfig | Out-String) | ConvertFrom-Json
    $chv = @($cfg.services.clickhouse.volumes)
    $hot = $chv | Where-Object { $_.target -eq "/var/lib/clickhouse" } | Select-Object -First 1
    $cold = $chv | Where-Object { $_.target -eq "/var/lib/clickhouse-cold" } | Select-Object -First 1
    $logs = $chv | Where-Object { $_.target -eq "/var/log/clickhouse-server" } | Select-Object -First 1
    if ((Normalize-HostPath $hot.source) -ne (Normalize-HostPath $HotPath)) { throw "Resolved Hot source is wrong." }
    if ((Normalize-HostPath $cold.source) -ne (Normalize-HostPath $ColdPath)) { throw "Resolved Cold source is wrong." }
    if ((Normalize-HostPath $logs.source) -ne (Normalize-HostPath $LogPath)) { throw "Resolved Logs source is wrong." }
    Write-Host "COMPOSE_MODEL_OK"

    docker compose @compose pull postgres clickhouse
    Assert-LastExitCode "Unable to pull PostgreSQL/ClickHouse images after reset."

    Write-Host "`n===== CREATE DATABASE CONTAINER SHELLS ONLY ====="

    docker compose @compose create postgres clickhouse
    Assert-LastExitCode "Unable to create database container shells."

    $pgName = "markorbit-data-engine-postgres-1"
    $chName = "markorbit-data-engine-clickhouse-1"
    $pg = ((docker inspect $pgName | Out-String) | ConvertFrom-Json)[0]
    $ch = ((docker inspect $chName | Out-String) | ConvertFrom-Json)[0]
    $pgMount = $pg.Mounts | Where-Object { $_.Destination -eq "/var/lib/postgresql/data" } | Select-Object -First 1
    $chHot = $ch.Mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse" } | Select-Object -First 1
    $chCold = $ch.Mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse-cold" } | Select-Object -First 1
    $chLogs = $ch.Mounts | Where-Object { $_.Destination -eq "/var/log/clickhouse-server" } | Select-Object -First 1
    if ($pgMount.Type -ne "volume" -or $pgMount.Name -ne "markorbit-data-engine_postgres_data") { throw "Created PostgreSQL mount is wrong." }
    if ($chHot.Type -ne "bind" -or (Normalize-HostPath $chHot.Source) -ne (Normalize-HostPath $HotPath)) { throw "Created Hot mount is wrong." }
    if ($chCold.Type -ne "bind" -or (Normalize-HostPath $chCold.Source) -ne (Normalize-HostPath $ColdPath)) { throw "Created Cold mount is wrong." }
    if ($chLogs.Type -ne "bind" -or (Normalize-HostPath $chLogs.Source) -ne (Normalize-HostPath $LogPath)) { throw "Created Logs mount is wrong." }
    Write-Host "CREATED_MOUNTS_OK"

    Write-Host "`n===== RESTORE COLD PGDATA ====="

    $pgVolume = "markorbit-data-engine_postgres_data"
    $volumeMount = "type=volume,source=$pgVolume,target=/target"
    docker run --rm --mount $volumeMount postgres:16-alpine sh -lc 'set -eu; if [ -n "$(find /target -mindepth 1 -maxdepth 1 -print -quit)" ]; then echo "target volume not empty" >&2; exit 42; fi'
    Assert-LastExitCode "Fresh PostgreSQL volume is not empty."

    $pgArchive = [string]$pgManifest.cold_pgdata_backup.path
    $pgArchiveDir = Split-Path -Parent $pgArchive
    $pgArchiveName = Split-Path -Leaf $pgArchive
    $backupMount = "type=bind,source=$pgArchiveDir,target=/backup,readonly"
    docker run --rm --mount $volumeMount --mount $backupMount -e "BACKUP_NAME=$pgArchiveName" postgres:16-alpine sh -lc 'set -eu; cd /target; tar -xzf "/backup/$BACKUP_NAME"'
    Assert-LastExitCode "Failed restoring cold PostgreSQL PGDATA backup."
    Write-Host "POSTGRES_PGDATA_RESTORED"

    Write-Host "`n===== RESTORE ANCILLARY MARKORBIT VOLUMES ====="

    foreach ($entry in @($prep.ancillary_volumes)) {
        $volumeName = [string]$entry.volume
        docker volume create $volumeName | Out-Null
        Assert-LastExitCode "Unable to create ancillary volume $volumeName"
        $archive = [string]$entry.archive
        $archiveDir = Split-Path -Parent $archive
        $archiveName = Split-Path -Leaf $archive
        $targetMount = "type=volume,source=$volumeName,target=/target"
        $archiveMount = "type=bind,source=$archiveDir,target=/backup,readonly"
        docker run --rm --mount $targetMount --mount $archiveMount -e "BACKUP_NAME=$archiveName" postgres:16-alpine sh -lc 'set -eu; cd /target; tar -xzf "/backup/$BACKUP_NAME"'
        Assert-LastExitCode "Unable to restore ancillary volume $volumeName"
        Write-Host "Restored ancillary volume: $volumeName"
    }
    Write-Host "ANCILLARY_VOLUMES_RESTORED"

    Write-Host "`n===== START DATABASES ONLY ====="

    docker start $pgName | Out-Host
    Assert-LastExitCode "Failed starting restored PostgreSQL."
    docker start $chName | Out-Host
    Assert-LastExitCode "Failed starting ClickHouse against external Hot/Cold data."
    $null = Wait-Healthy $pgName
    $null = Wait-Healthy $chName
    Write-Host "DATABASES_HEALTHY"

    Write-Host "`n===== VERIFY POSTGRES INVENTORY ====="

    $dbInventory = @(docker exec $pgName sh -lc 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "|" -c "SELECT datname, pg_database_size(datname) FROM pg_database WHERE datistemplate = false ORDER BY datname;"')
    Assert-LastExitCode "Unable to query restored PostgreSQL inventory."
    $dbInventory | ForEach-Object { Write-Host "DB: $_" }
    $expectedControl = @($pgManifest.databases | Where-Object { $_ -match '^markorbit_control\|' } | Select-Object -First 1)
    if ($expectedControl.Count -ne 1) { throw "Expected markorbit_control inventory missing from backup manifest." }
    $expectedBytes = [int64](($expectedControl[0] -split '\|')[1])
    $actualControl = @($dbInventory | Where-Object { $_ -match '^markorbit_control\|' } | Select-Object -First 1)
    if ($actualControl.Count -ne 1) { throw "Restored markorbit_control database is missing." }
    $actualBytes = [int64](($actualControl[0] -split '\|')[1])
    if ($actualBytes -lt [int64]($expectedBytes * 0.98) -or $actualBytes -gt [int64]($expectedBytes * 1.02)) {
        throw "Restored markorbit_control size is outside 2% of the pre-reset inventory."
    }
    Write-Host "POSTGRES_INVENTORY_OK"

    Write-Host "`n===== VERIFY CN SERVING AND EQUIVALENCE ====="

    powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-serving-state.ps1 -Compact
    Assert-LastExitCode "CN serving-state did not PASS after reset restore."

    $postProfile = Join-Path $prepDir.FullName "cn_capacity_post_docker_reset.json"
    powershell.exe -ExecutionPolicy Bypass -File .\scripts\profile-cn-hot-warm-capacity.ps1 -OutputPath $postProfile
    Assert-LastExitCode "CN capacity profile failed after reset restore."
    $post = Get-Content -Raw -LiteralPath $postProfile | ConvertFrom-Json
    if ([int64]$post.active_totals.rows_from_parts -ne [int64]$prep.cn_active_rows) { throw "CN active row count changed across Docker reset." }
    if ([int64]$post.active_totals.bytes_on_disk -ne [int64]$prep.cn_active_bytes) { throw "CN active bytes changed across Docker reset." }
    Write-Host "CN_ROW_AND_BYTE_EQUIVALENCE_OK"

    $running = @(docker ps --format "{{.Names}}")
    if ($running -contains "markorbit-data-engine-api-1" -or $running -contains "markorbit-data-engine-worker-1") {
        throw "API or persistent worker unexpectedly running after restore."
    }
    Write-Host "API_WORKER_STOPPED_OK"

    $finalVhdxBytes = (Get-Item -LiteralPath $dVhdx).Length
    Write-Host ("Docker VHDX after database restore: {0:N2} GiB" -f ($finalVhdxBytes / 1GB))
    Write-Host "Post-reset CN profile: $postProfile"
    Write-Host "DOCKER_DATA_RESET_RESTORE_OK"
}
finally {
    Pop-Location
}
