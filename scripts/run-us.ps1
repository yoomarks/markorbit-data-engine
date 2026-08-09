$ErrorActionPreference = "Stop"

$apply = Join-Path $PSScriptRoot "apply-us-m1-schema.ps1"
powershell.exe -ExecutionPolicy Bypass -File $apply
if ($LASTEXITCODE -ne 0) {
    throw "US M1 schema gate failed; ingestion was not started."
}

Write-Host "Starting dedicated one-shot US worker..."
docker compose run --rm --no-deps worker python -m app.us.run_once
if ($LASTEXITCODE -ne 0) {
    throw "US one-shot worker exited with code $LASTEXITCODE."
}
