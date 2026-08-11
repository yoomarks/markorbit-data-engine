param(
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
        throw "Persistent worker is running. Stop it before CN goods history commit: docker compose stop worker"
    }

    $pythonArgs = @("-m", "app.cn.storage_v2_goods_commit")
    if ($Compact) {
        $pythonArgs += "--compact"
    }

    docker compose run --rm --no-deps `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @pythonArgs

    if ($LASTEXITCODE -ne 0) {
        throw "CN goods Storage V2 single-process commit failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
