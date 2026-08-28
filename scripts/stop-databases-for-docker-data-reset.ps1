param(
    [string]$RecoveryRoot = "F:\MarkOrbitData\recovery\docker-data-reset",
    [string]$PostgresContainer = "markorbit-data-engine-postgres-1",
    [string]$ClickHouseContainer = "markorbit-data-engine-clickhouse-1"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

function Get-State([string]$Name) {
    $raw = docker inspect $Name 2>$null
    Assert-LastExitCode "Container not found: $Name"
    return (($raw | Out-String) | ConvertFrom-Json)[0]
}

try {
    Write-Host "`n===== DATABASE CLEAN STOP FOR DOCKER DATA RESET ====="

    docker info | Out-Null
    Assert-LastExitCode "Docker Engine is unavailable."

    $prepDir = Get-ChildItem -LiteralPath $RecoveryRoot -Directory |
        Sort-Object LastWriteTime -Descending |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "manifest.json") } |
        Select-Object -First 1
    if ($null -eq $prepDir) { throw "No accepted Docker reset preparation manifest found." }
    $prep = Get-Content -Raw -LiteralPath (Join-Path $prepDir.FullName "manifest.json") | ConvertFrom-Json
    if ($prep.schema_version -ne "DOCKER_DATA_RESET_PREPARATION_V1" -or [bool]$prep.destructive_action_performed) {
        throw "Docker reset preparation manifest is not accepted."
    }
    Write-Host "PREPARATION_MANIFEST_OK"

    $running = @(docker ps --format "{{.Names}}")
    if ($running -contains "markorbit-data-engine-api-1") { throw "API must be stopped before database clean stop." }
    if ($running -contains "markorbit-data-engine-worker-1") { throw "Persistent worker must be stopped before database clean stop." }

    $pg = Get-State $PostgresContainer
    $ch = Get-State $ClickHouseContainer
    $pgHealth = if ($pg.State.Health) { $pg.State.Health.Status } else { "none" }
    $chHealth = if ($ch.State.Health) { $ch.State.Health.Status } else { "none" }
    if ($pg.State.Status -ne "running" -or $pgHealth -ne "healthy") { throw "PostgreSQL must be healthy before clean stop." }
    if ($ch.State.Status -ne "running" -or $chHealth -ne "healthy") { throw "ClickHouse must be healthy before clean stop." }
    Write-Host "DATABASES_HEALTHY_BEFORE_STOP"

    docker stop --timeout 60 $PostgresContainer | Out-Host
    Assert-LastExitCode "Failed to clean-stop PostgreSQL."
    docker stop --timeout 120 $ClickHouseContainer | Out-Host
    Assert-LastExitCode "Failed to clean-stop ClickHouse."

    $pg = Get-State $PostgresContainer
    $ch = Get-State $ClickHouseContainer
    if ($pg.State.Status -ne "exited") { throw "PostgreSQL did not reach exited state." }
    if ($ch.State.Status -ne "exited") { throw "ClickHouse did not reach exited state." }

    $running = @(docker ps --format "{{.Names}}")
    if ($running -contains $PostgresContainer -or $running -contains $ClickHouseContainer) {
        throw "A database container is still running after clean stop."
    }

    Write-Host "POSTGRES_CLEAN_STOP_OK"
    Write-Host "CLICKHOUSE_CLEAN_STOP_OK"
    Write-Host "DATABASES_CLEANLY_STOPPED_FOR_DOCKER_RESET"
    Write-Host "Docker Desktop is still running. The operator did not reset or delete Docker data."
}
finally {
    Pop-Location
}
