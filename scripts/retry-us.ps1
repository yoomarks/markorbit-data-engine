param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts
)

$ErrorActionPreference = "Stop"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $PSScriptRoot "assert-domain-apply-gate.ps1") `
    -TargetDomain "US_APPLICATION" `
    -ExpectedApplicationHistoryParts $ExpectedHistoryParts
if ($LASTEXITCODE -ne 0) {
    throw "US Application apply gate failed; retry was not started."
}

$apply = Join-Path $PSScriptRoot "apply-us-m1-schema.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $apply
if ($LASTEXITCODE -ne 0) {
    throw "US M1 schema gate failed; retry was not started."
}

Write-Host "Retrying earliest failed/interrupted US package..."
docker compose run --rm --no-deps worker python -m app.us.retry_once
if ($LASTEXITCODE -ne 0) {
    throw "US retry worker exited with code $LASTEXITCODE."
}
