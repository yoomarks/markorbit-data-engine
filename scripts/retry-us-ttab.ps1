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
if ($LASTEXITCODE -ne 0) { throw "US TTAB apply gate failed; retry was not started." }

docker compose run --rm --no-deps worker python -m app.us_ttab.retry_once
if ($LASTEXITCODE -ne 0) { throw "US TTAB retry failed." }
