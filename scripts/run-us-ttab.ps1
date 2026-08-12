param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedApplicationHistoryParts
)

$ErrorActionPreference = "Stop"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $PSScriptRoot "assert-domain-apply-gate.ps1") `
    -TargetDomain "US_TTAB" `
    -ExpectedApplicationHistoryParts $ExpectedApplicationHistoryParts
if ($LASTEXITCODE -ne 0) { throw "US TTAB apply gate failed; ingestion was not started." }

powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "apply-us-ttab-schema.ps1")
if ($LASTEXITCODE -ne 0) { throw "US TTAB schema gate failed." }

docker compose run --rm --no-deps worker python -m app.us_ttab.run_once
if ($LASTEXITCODE -ne 0) { throw "US TTAB one-shot ingestion failed." }
