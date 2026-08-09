$ErrorActionPreference = "Stop"

powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "apply-us-assignment-schema.ps1")
if ($LASTEXITCODE -ne 0) { throw "US assignment schema gate failed." }

docker compose run --rm --no-deps worker python -m app.us_assignment.retry_once
if ($LASTEXITCODE -ne 0) { throw "US assignment retry failed." }
