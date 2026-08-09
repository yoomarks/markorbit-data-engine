$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$schemaPath = Join-Path $repoRoot "database/clickhouse/init/003_m16_goods_lifecycle.sql"

if (-not (Test-Path $schemaPath)) {
    throw "Missing M1.6 schema file: $schemaPath"
}

Write-Host "Applying M1.6 CN goods lifecycle schema..."
Get-Content -Raw $schemaPath | docker compose exec -T clickhouse clickhouse-client --multiquery
if ($LASTEXITCODE -ne 0) {
    throw "ClickHouse M1.6 schema apply failed."
}

Write-Host "M1.6 CN goods lifecycle schema applied."
