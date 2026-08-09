$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$schemaPath = Join-Path $repoRoot "database/clickhouse/init/004_us_m1_core.sql"

if (-not (Test-Path $schemaPath)) {
    throw "Missing US M1 schema file: $schemaPath"
}

Write-Host "Applying US M1 ClickHouse schema..."
Get-Content -Raw $schemaPath | docker compose exec -T clickhouse clickhouse-client --multiquery
if ($LASTEXITCODE -ne 0) {
    throw "ClickHouse US M1 schema apply failed."
}

Write-Host "US M1 ClickHouse schema applied."
