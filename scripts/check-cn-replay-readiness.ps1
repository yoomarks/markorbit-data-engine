param(
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $postgresId = docker compose ps --status running -q postgres
    if (-not $postgresId) {
        throw "PostgreSQL is not running. Start only the database services first: docker compose up -d postgres clickhouse"
    }

    $clickhouseId = docker compose ps --status running -q clickhouse
    if (-not $clickhouseId) {
        throw "ClickHouse is not running. Start only the database services first: docker compose up -d postgres clickhouse"
    }

    $persistentWorkerId = docker compose ps --status running -q worker
    $diagnosticArgs = @("-m", "app.cn.replay_readiness_cli")
    if ($persistentWorkerId) {
        $diagnosticArgs += "--persistent-worker-running"
    }
    if ($Compact) {
        $diagnosticArgs += "--compact"
    }

    # Read-only diagnostic: mount the checked-out application code so the report
    # matches the current branch without rebuilding or starting the persistent worker.
    docker compose run --rm --no-deps `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @diagnosticArgs

    if ($LASTEXITCODE -ne 0) {
        throw "CN replay readiness diagnostic failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
