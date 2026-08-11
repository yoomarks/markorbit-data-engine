param(
    [ValidateSet("Plan", "Apply", "Rollback", "Finalize")]
    [string]$Mode = "Plan",
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $clickhouseId = docker compose ps --status running -q clickhouse
    if (-not $clickhouseId) {
        throw "ClickHouse is not running. Start only the database services first: docker compose up -d postgres clickhouse"
    }

    $workerId = docker compose ps --status running -q worker
    if ($workerId) {
        throw "Persistent worker is running. Stop it before CN goods history compaction: docker compose stop worker"
    }

    $modeValue = $Mode.ToLowerInvariant()
    $args = @("-m", "app.cn.storage_v2_goods_compaction", "--mode", $modeValue)
    if ($Compact) {
        $args += "--compact"
    }

    # Mount only the checked-out application code. This keeps the operation tied
    # to the reviewed branch/main without rebuilding or starting the persistent worker.
    docker compose run --rm --no-deps `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @args

    if ($LASTEXITCODE -ne 0) {
        throw "CN goods Storage V2 compaction failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
