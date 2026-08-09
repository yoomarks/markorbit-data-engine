$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$schemaPath = Join-Path $repoRoot "database/clickhouse/init/009_us_assignment_m10.sql"
if (-not (Test-Path $schemaPath)) {
    throw "Missing US assignment schema file: $schemaPath"
}

Get-Content -Raw $schemaPath | docker compose exec -T clickhouse clickhouse-client --multiquery
if ($LASTEXITCODE -ne 0) {
    throw "US assignment ClickHouse schema apply failed."
}

docker compose run --rm --no-deps worker python -c "from app.us_assignment.migrations import ensure_assignment_schema; ensure_assignment_schema(); print('US assignment schema guard PASS')"
if ($LASTEXITCODE -ne 0) {
    throw "US assignment schema runtime guard failed."
}

Write-Host "US Assignment M1.0 schema applied."
