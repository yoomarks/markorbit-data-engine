$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$schemaPaths = @(
    (Join-Path $repoRoot "database/clickhouse/init/004_us_m1_core.sql"),
    (Join-Path $repoRoot "database/clickhouse/init/005_us_m11_real_tdxf.sql"),
    (Join-Path $repoRoot "database/clickhouse/init/006_us_m12_snapshot_semantics.sql"),
    (Join-Path $repoRoot "database/clickhouse/init/007_us_m13_official_fact_families.sql")
)

foreach ($schemaPath in $schemaPaths) {
    if (-not (Test-Path $schemaPath)) {
        throw "Missing US schema file: $schemaPath"
    }
}

Write-Host "Applying US M1.3 ClickHouse schema..."
foreach ($schemaPath in $schemaPaths) {
    Get-Content -Raw $schemaPath | docker compose exec -T clickhouse clickhouse-client --multiquery
    if ($LASTEXITCODE -ne 0) {
        throw "ClickHouse US schema apply failed: $schemaPath"
    }
}

Write-Host "US M1.3 ClickHouse schema applied."
