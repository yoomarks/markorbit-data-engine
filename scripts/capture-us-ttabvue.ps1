param(
    [Parameter(Mandatory=$true)][string]$ProceedingNumber,
    [Parameter(Mandatory=$true)][ValidateSet("OPP", "CAN", "EXA", "EXT")][string]$ProceedingType,
    [Parameter(Mandatory=$true)][string]$SnapshotAt
)
$ErrorActionPreference = "Stop"

docker compose run --rm --no-deps worker python -m app.us_ttab.capture_ttabvue `
    --pno $ProceedingNumber `
    --pty $ProceedingType `
    --snapshot-at $SnapshotAt
if ($LASTEXITCODE -ne 0) { throw "TTABVUE raw XML capture/registration failed." }
