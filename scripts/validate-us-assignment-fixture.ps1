$ErrorActionPreference = "Stop"

powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "apply-us-assignment-schema.ps1")
if ($LASTEXITCODE -ne 0) { throw "US assignment schema gate failed." }

docker compose run --rm --no-deps worker python -m app.us_assignment.validate_fixture
if ($LASTEXITCODE -ne 0) { throw "US Assignment M1.0 runtime fixture failed." }

Write-Host "US Assignment M1.0 runtime fixture passed and cleaned up."
