$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$clickhouseSchemaPaths = @(
    (Join-Path $repoRoot "database/clickhouse/init/004_us_m1_core.sql"),
    (Join-Path $repoRoot "database/clickhouse/init/005_us_m11_real_tdxf.sql"),
    (Join-Path $repoRoot "database/clickhouse/init/006_us_m12_snapshot_semantics.sql"),
    (Join-Path $repoRoot "database/clickhouse/init/007_us_m13_official_fact_families.sql"),
    (Join-Path $repoRoot "database/clickhouse/init/008_us_m14_change_history.sql")
)
$postgresSchemaPaths = @(
    (Join-Path $repoRoot "database/postgres/init/002_us_status_reference.sql"),
    (Join-Path $repoRoot "database/postgres/init/003_us_semantic_reference.sql"),
    (Join-Path $repoRoot "database/postgres/init/004_us_event_roles.sql")
)

foreach ($schemaPath in @($clickhouseSchemaPaths + $postgresSchemaPaths)) {
    if (-not (Test-Path $schemaPath)) {
        throw "Missing US schema file: $schemaPath"
    }
}

Write-Host "Applying US M1.4 ClickHouse schema..."
foreach ($schemaPath in $clickhouseSchemaPaths) {
    Get-Content -Raw $schemaPath | docker compose exec -T clickhouse clickhouse-client --multiquery
    if ($LASTEXITCODE -ne 0) {
        throw "ClickHouse US schema apply failed: $schemaPath"
    }
}

Write-Host "Applying US semantic/reference PostgreSQL schema..."
foreach ($schemaPath in $postgresSchemaPaths) {
    Get-Content -Raw $schemaPath | docker compose exec -T postgres sh -lc 'psql -v ON_ERROR_STOP=1 -U $POSTGRES_USER -d $POSTGRES_DB'
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL US reference schema apply failed: $schemaPath"
    }
}

Write-Host "Running US schema runtime guard against current repository code..."
# The target host may retain an older worker image. Bind-mount the exact checked-out
# app tree so the runtime guard and schema-version marker always come from the
# current repository revision without rebuilding or restarting persistent services.
docker compose run --rm --no-deps -T `
    --volume "${repoRoot}\app:/app/app:ro" `
    worker python -c "from app.us.migrations import ensure_us_m1_schema; ensure_us_m1_schema(); print('US schema runtime guard PASS')"
if ($LASTEXITCODE -ne 0) {
    throw "US schema runtime guard failed."
}

Write-Host "US M1.4 + semantic/reference/event-role schema applied."
