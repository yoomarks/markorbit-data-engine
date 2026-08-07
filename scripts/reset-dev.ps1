$ErrorActionPreference = "Stop"

Write-Host "M1.5 uses reset-m15.ps1 for schema-safe DEV resets." -ForegroundColor Yellow
& "$PSScriptRoot\reset-m15.ps1"
