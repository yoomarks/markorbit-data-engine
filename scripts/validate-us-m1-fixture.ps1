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

Write-Host "US M1.2 live runtime fixtures passed and cleaned up."
