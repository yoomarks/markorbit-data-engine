$ErrorActionPreference = "Stop"

powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "apply-us-ttab-schema.ps1")
if ($LASTEXITCODE -ne 0) { throw "US TTAB schema gate failed." }

docker compose run --rm --no-deps worker python -m app.us_ttab.run_once
if ($LASTEXITCODE -ne 0) { throw "US TTAB one-shot ingestion failed." }
