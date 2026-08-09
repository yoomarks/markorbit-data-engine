param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$SnapshotAt,
    [Parameter(Mandatory=$true)]
    [ValidateSet(
        "TTABVUE_PROCEEDING_RAWXML_SNAPSHOT",
        "TTAB_BULK_DAILY_XML",
        "TTAB_BULK_HISTORICAL_XML"
    )]
    [string]$SourceKind
)
$ErrorActionPreference = "Stop"

powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "apply-us-ttab-schema.ps1")
if ($LASTEXITCODE -ne 0) { throw "US TTAB schema gate failed." }

docker compose run --rm --no-deps worker python -m app.us_ttab.register_source $Path --snapshot-at $SnapshotAt --source-kind $SourceKind
if ($LASTEXITCODE -ne 0) { throw "US TTAB source registration failed." }
