param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedApplicationHistoryParts
)

$ErrorActionPreference = "Stop"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $PSScriptRoot "assert-domain-apply-gate.ps1") `
    -TargetDomain "US_ASSIGNMENT" `
    -ExpectedApplicationHistoryParts $ExpectedApplicationHistoryParts
if ($LASTEXITCODE -ne 0) { throw "US Assignment apply gate failed; ingestion was not started." }

powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "apply-us-assignment-schema.ps1")
if ($LASTEXITCODE -ne 0) { throw "US assignment schema gate failed." }

docker compose run --rm --no-deps worker python -m app.us_assignment.run_once
if ($LASTEXITCODE -ne 0) { throw "US assignment one-shot ingestion failed." }
