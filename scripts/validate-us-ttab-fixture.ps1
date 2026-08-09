$ErrorActionPreference = "Stop"

powershell.exe -ExecutionPolicy Bypass -File .\scripts\apply-us-ttab-schema.ps1
if ($LASTEXITCODE -ne 0) { throw "US TTAB schema apply failed before fixture." }

docker compose run --rm --no-deps worker python -m app.us_ttab.validate_fixture
if ($LASTEXITCODE -ne 0) { throw "US TTAB M1.0 live fixture failed." }

Write-Host "US TTAB M1.0 fixture PASS."
