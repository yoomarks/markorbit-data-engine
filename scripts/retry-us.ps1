$ErrorActionPreference = "Stop"

$apply = Join-Path $PSScriptRoot "apply-us-m1-schema.ps1"
powershell.exe -ExecutionPolicy Bypass -File $apply
if ($LASTEXITCODE -ne 0) {
    throw "US M1 schema gate failed; retry was not started."
}

Write-Host "Retrying earliest failed/interrupted US package..."
docker compose run --rm --no-deps worker python -m app.us.retry_once
if ($LASTEXITCODE -ne 0) {
    throw "US retry worker exited with code $LASTEXITCODE."
}
