$ErrorActionPreference = "Stop"

$apply = Join-Path $PSScriptRoot "apply-us-m1-schema.ps1"
powershell.exe -ExecutionPolicy Bypass -File $apply
if ($LASTEXITCODE -ne 0) {
    throw "US M1 schema gate failed; runtime fixtures were not started."
}

Write-Host "Running US M1.1 real-TDXF regression fixture..."
docker compose run --rm --no-deps worker python -m app.us.validate_fixture
if ($LASTEXITCODE -ne 0) {
    throw "US M1.1 real-TDXF runtime fixture failed."
}

Write-Host "Running US M1.2 child snapshot replacement fixture..."
docker compose run --rm --no-deps worker python -m app.us.validate_snapshot_fixture
if ($LASTEXITCODE -ne 0) {
    throw "US M1.2 child snapshot runtime fixture failed."
}

Write-Host "Running US M1.3 official fact families fixture..."
docker compose run --rm --no-deps worker python -m app.us.validate_official_fact_fixture
if ($LASTEXITCODE -ne 0) {
    throw "US M1.3 official fact families runtime fixture failed."
}

Write-Host "Running US semantic reference + UNKNOWN-first interpretation fixture..."
docker compose run --rm --no-deps worker python -m app.us.validate_status_reference_fixture
if ($LASTEXITCODE -ne 0) {
    throw "US semantic reference/interpretation runtime fixture failed."
}

Write-Host "Running US maintenance + official reference-pack fixture..."
docker compose run --rm --no-deps worker python -m app.us.validate_maintenance_fixture
if ($LASTEXITCODE -ne 0) {
    throw "US maintenance/reference-pack runtime fixture failed."
}

Write-Host "US M1.3 + semantic + maintenance live runtime fixtures passed and cleaned up."
