param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$SnapshotAt,
    [string]$SourceKind = "TTABVUE_PROCEEDING_RAWXML_SNAPSHOT"
)
$ErrorActionPreference = "Stop"

docker compose run --rm --no-deps worker python -m app.us_ttab.register_source $Path --snapshot-at $SnapshotAt --source-kind $SourceKind
if ($LASTEXITCODE -ne 0) { throw "US TTAB source registration failed." }
