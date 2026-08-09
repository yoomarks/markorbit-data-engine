$ErrorActionPreference = "Stop"

Write-Warning "reset-m15.ps1 is a legacy entry point. MarkOrbit Data Engine is M1.6; delegating to reset-m16.ps1."
& (Join-Path $PSScriptRoot "reset-m16.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "reset-m16.ps1 failed with exit code $LASTEXITCODE."
}
