$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$schemaPaths = @(
    (Join-Path $repoRoot "database/clickhouse/init/010_us_ttab_m10.sql"),
    (Join-Path $repoRoot "database/clickhouse/init/011_us_ttab_m11_real_rawxml.sql")
)
foreach ($schemaPath in $schemaPaths) {
    if (-not (Test-Path $schemaPath)) {
        throw "Missing US TTAB schema file: $schemaPath"
    }
    Get-Content -Raw $schemaPath | docker compose exec -T clickhouse clickhouse-client --multiquery
    if ($LASTEXITCODE -ne 0) {
        throw "US TTAB ClickHouse schema apply failed: $schemaPath"
    }
}

docker compose run --rm --no-deps worker python -c "from app.us_ttab.migrations import ensure_ttab_schema; ensure_ttab_schema(); print('US TTAB M1.1 schema guard PASS')"
if ($LASTEXITCODE -ne 0) {
    throw "US TTAB schema runtime guard failed."
}

Write-Host "US TTAB M1.1 schema applied."
