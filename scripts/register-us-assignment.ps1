param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$EffectiveDate,
    [Parameter(Mandatory = $true)][ValidateSet("DAILY_ASSIGNMENT_XML", "ASSIGNMENT_SNAPSHOT_XML")][string]$SourceKind
)

$ErrorActionPreference = "Stop"

powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "apply-us-assignment-schema.ps1")
if ($LASTEXITCODE -ne 0) { throw "US assignment schema gate failed." }

docker compose run --rm --no-deps worker python -m app.us_assignment.register_source $Path --effective-date $EffectiveDate --source-kind $SourceKind
if ($LASTEXITCODE -ne 0) { throw "US assignment source registration failed." }
