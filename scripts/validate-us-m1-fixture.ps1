$ErrorActionPreference = "Stop"

$apply = Join-Path $PSScriptRoot "apply-us-m1-schema.ps1"
powershell.exe -ExecutionPolicy Bypass -File $apply
if ($LASTEXITCODE -ne 0) {
    throw "US M1 schema gate failed; runtime fixture was not started."
}

Write-Host "Running US M1 live ClickHouse runtime fixture..."
docker compose run --rm --no-deps worker python -m app.us.validate_fixture
if ($LASTEXITCODE -ne 0) {
    throw "US M1 live runtime fixture failed."
}

Write-Host "US M1 live runtime fixture passed and cleaned up."
