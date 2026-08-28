param(
    [string]$BackupRoot = "F:\MarkOrbitData\recovery\postgres-before-docker-reset",
    [string]$PostgresContainer = "markorbit-data-engine-postgres-1",
    [string]$PostgresVolume = "markorbit-data-engine_postgres_data"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

function Get-ContainerState([string]$Name) {
    $raw = docker inspect $Name 2>$null
    Assert-LastExitCode "Container not found: $Name"
    return (($raw | Out-String) | ConvertFrom-Json)[0]
}

function Wait-PostgresHealthy([string]$Name, [int]$Minutes = 5) {
    $deadline = (Get-Date).AddMinutes($Minutes)
    do {
        $state = Get-ContainerState $Name
        $health = if ($state.State.Health) { $state.State.Health.Status } else { "none" }
        Write-Host "PostgreSQL status=$($state.State.Status) health=$health"
        if ($state.State.Status -eq "running" -and $health -eq "healthy") { return }
        if ($state.State.Status -in @("dead", "exited")) {
            docker logs --tail 100 $Name
            throw "PostgreSQL exited during backup operator."
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    throw "PostgreSQL did not become healthy within the allowed window."
}

try {
    Write-Host "`n===== POSTGRES BACKUP PREFLIGHT ====="

    docker info | Out-Null
    Assert-LastExitCode "Docker Engine is unavailable."

    $pg = Get-ContainerState $PostgresContainer
    $health = if ($pg.State.Health) { $pg.State.Health.Status } else { "none" }
    if ($pg.State.Status -ne "running" -or $health -ne "healthy") {
        throw "PostgreSQL must be running and healthy before backup."
    }

    $pgMount = $pg.Mounts | Where-Object { $_.Destination -eq "/var/lib/postgresql/data" }
    if ($null -eq $pgMount -or $pgMount.Type -ne "volume" -or $pgMount.Name -ne $PostgresVolume) {
        throw "PostgreSQL is not mounted from the expected persistent volume."
    }

    $runningNames = @(docker ps --format "{{.Names}}")
    if ($runningNames -contains "markorbit-data-engine-api-1") {
        throw "API container is running. Backup/reset preparation remains blocked."
    }
    if ($runningNames -contains "markorbit-data-engine-worker-1") {
        throw "Persistent worker is running. Backup/reset preparation remains blocked."
    }

    docker volume inspect $PostgresVolume | Out-Null
    Assert-LastExitCode "PostgreSQL volume is unavailable."

    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupDir = Join-Path $BackupRoot $stamp
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

    Write-Host "Backup directory: $backupDir"
    Write-Host "POSTGRES_PREFLIGHT_OK"

    Write-Host "`n===== READ-ONLY POSTGRES INVENTORY ====="

    $pgVersion = (docker exec $PostgresContainer sh -lc 'postgres --version').Trim()
    Assert-LastExitCode "Unable to read PostgreSQL version."

    $dbInventory = @(docker exec $PostgresContainer sh -lc 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "|" -c "SELECT datname, pg_database_size(datname) FROM pg_database WHERE datistemplate = false ORDER BY datname;"')
    Assert-LastExitCode "Unable to inventory PostgreSQL databases."

    Write-Host "Version: $pgVersion"
    $dbInventory | ForEach-Object { Write-Host "DB: $_" }

    Write-Host "`n===== LOGICAL CLUSTER BACKUP ====="

    $logicalName = "postgres_cluster_$stamp.sql.gz"
    $logicalHost = Join-Path $backupDir $logicalName
    $logicalTmp = "/tmp/markorbit_postgres_cluster_backup.sql.gz"

    docker exec $PostgresContainer sh -lc 'set -eu; rm -f /tmp/markorbit_postgres_cluster_backup.sql.gz; pg_dumpall -U "$POSTGRES_USER" | gzip -1 -c > /tmp/markorbit_postgres_cluster_backup.sql.gz; gzip -t /tmp/markorbit_postgres_cluster_backup.sql.gz'
    Assert-LastExitCode "Logical pg_dumpall backup failed."

    docker cp "${PostgresContainer}:$logicalTmp" $logicalHost
    Assert-LastExitCode "Failed copying logical PostgreSQL backup to F:."

    docker exec $PostgresContainer sh -lc 'rm -f /tmp/markorbit_postgres_cluster_backup.sql.gz'
    Assert-LastExitCode "Failed removing temporary logical dump from container."

    if (-not (Test-Path -LiteralPath $logicalHost -PathType Leaf) -or (Get-Item -LiteralPath $logicalHost).Length -lt 1024) {
        throw "Logical backup file is missing or unexpectedly small."
    }

    $backupBindRo = "type=bind,source=$backupDir,target=/backup,readonly"
    docker run --rm --mount $backupBindRo -e "BACKUP_NAME=$logicalName" postgres:16-alpine sh -lc 'gzip -t "/backup/$BACKUP_NAME"'
    Assert-LastExitCode "Host-side logical backup verification failed."

    $logicalHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $logicalHost).Hash.ToLowerInvariant()
    Write-Host "Logical backup SHA256: $logicalHash"
    Write-Host "LOGICAL_BACKUP_OK"

    Write-Host "`n===== CLEAN STOP POSTGRES FOR COLD VOLUME BACKUP ====="

    docker stop --timeout 60 $PostgresContainer | Out-Host
    Assert-LastExitCode "Failed to stop PostgreSQL cleanly."

    $stopped = Get-ContainerState $PostgresContainer
    if ($stopped.State.Status -ne "exited") {
        throw "PostgreSQL did not reach exited state before cold volume backup."
    }
    Write-Host "POSTGRES_CLEAN_STOP_OK"

    Write-Host "`n===== COLD PGDATA VOLUME BACKUP ====="

    $rawName = "postgres_pgdata_$stamp.tar.gz"
    $rawHost = Join-Path $backupDir $rawName
    $volumeMount = "type=volume,source=$PostgresVolume,target=/source,readonly"
    $backupBindRw = "type=bind,source=$backupDir,target=/backup"

    docker run --rm --mount $volumeMount --mount $backupBindRw -e "BACKUP_NAME=$rawName" postgres:16-alpine sh -lc 'set -eu; cd /source; tar -czf "/backup/$BACKUP_NAME" .'
    Assert-LastExitCode "Cold PostgreSQL volume backup failed."

    if (-not (Test-Path -LiteralPath $rawHost -PathType Leaf) -or (Get-Item -LiteralPath $rawHost).Length -lt 1024) {
        throw "Cold PGDATA backup is missing or unexpectedly small."
    }

    docker run --rm --mount $backupBindRo -e "BACKUP_NAME=$rawName" postgres:16-alpine sh -lc 'set -eu; gzip -t "/backup/$BACKUP_NAME"; tar -tzf "/backup/$BACKUP_NAME" >/dev/null'
    Assert-LastExitCode "Cold PGDATA backup verification failed."

    $rawHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $rawHost).Hash.ToLowerInvariant()
    Write-Host "Cold PGDATA SHA256: $rawHash"
    Write-Host "COLD_PGDATA_BACKUP_OK"

    Write-Host "`n===== RESTART POSTGRES ====="

    docker start $PostgresContainer | Out-Host
    Assert-LastExitCode "Failed to restart PostgreSQL after backup."
    Wait-PostgresHealthy $PostgresContainer
    Write-Host "POSTGRES_RESTORED_HEALTHY"

    $manifestPath = Join-Path $backupDir "manifest.json"
    $manifest = [ordered]@{
        schema_version = "POSTGRES_BEFORE_DOCKER_DATA_RESET_V1"
        created_at = (Get-Date).ToString("o")
        source_container = $PostgresContainer
        source_volume = $PostgresVolume
        postgres_version = $pgVersion
        databases = @($dbInventory)
        logical_backup = [ordered]@{
            path = $logicalHost
            bytes = (Get-Item -LiteralPath $logicalHost).Length
            sha256 = $logicalHash
            gzip_verified = $true
        }
        cold_pgdata_backup = [ordered]@{
            path = $rawHost
            bytes = (Get-Item -LiteralPath $rawHost).Length
            sha256 = $rawHash
            gzip_verified = $true
            tar_listing_verified = $true
        }
        postgres_restarted_healthy = $true
        api_worker_remained_stopped = $true
    }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding utf8

    Write-Host "Manifest: $manifestPath"
    Write-Host "POSTGRES_DUAL_BACKUP_OK"
}
finally {
    try {
        $raw = docker inspect $PostgresContainer 2>$null
        if ($LASTEXITCODE -eq 0) {
            $obj = (($raw | Out-String) | ConvertFrom-Json)[0]
            if ($obj.State.Status -ne "running") {
                docker start $PostgresContainer | Out-Null
            }
        }
    }
    catch {
        Write-Warning "PostgreSQL automatic restart attempt failed: $($_.Exception.Message)"
    }
    Pop-Location
}
