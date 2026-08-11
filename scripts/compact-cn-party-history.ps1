param(
    [ValidateSet("Plan", "Commit", "Status")]
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
        throw "Persistent worker is running. Stop it before CN party-history compaction: docker compose stop worker"
    }

    $pythonArgs = @(
        "-m", "app.cn.storage_v2_party_history_compaction",
        "--mode", $Mode.ToLowerInvariant()
    )
    if ($Compact) {
        $pythonArgs += "--compact"
    }

    docker compose run --rm --no-deps `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @pythonArgs

    if ($LASTEXITCODE -ne 0) {
        throw "CN party-history Storage V2 compaction failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
