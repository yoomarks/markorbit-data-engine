$ErrorActionPreference = "Stop"

Write-Warning "validate-m15.ps1 is a legacy entry point. MarkOrbit Data Engine is M1.6; delegating to validate-m16.ps1."
& (Join-Path $PSScriptRoot "validate-m16.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "validate-m16.ps1 failed with exit code $LASTEXITCODE."
}
