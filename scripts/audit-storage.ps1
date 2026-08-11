param(
    [switch]$Deep,
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $clickhouseId = docker compose ps -q clickhouse
    if (-not $clickhouseId) {
        throw "ClickHouse is not running. Start only the database services first: docker compose up -d postgres clickhouse"
    }

    $args = @("-m", "app.storage_audit")
    if ($Deep) {
        $args += "--deep"
    }
    if ($Compact) {
        $args += "--compact"
    }

    # Mount only the checked-out application code. This keeps the audit aligned
    # with the current branch without rebuilding or starting the persistent worker.
    docker compose run --rm --no-deps `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @args

    if ($LASTEXITCODE -ne 0) {
        throw "Storage audit failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
