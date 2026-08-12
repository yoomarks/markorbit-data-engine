param(
    [Parameter(Mandatory = $true)]
    [string]$File,

    [string]$SourceName = "",

    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$resolved = Resolve-Path -LiteralPath $File -ErrorAction Stop
$inputPath = $resolved.Path
$inputDir = Split-Path -Parent $inputPath
$inputName = Split-Path -Leaf $inputPath
$containerPath = "/contact-input/$inputName"

$argsList = @(
    "compose", "run", "--build", "--rm", "--no-deps",
    "-v", "${inputDir}:/contact-input:ro",
    "worker", "python", "-m", "app.contact_ingest.cli",
    "--input", $containerPath
)

if ($SourceName) {
    $argsList += @("--source-name", $SourceName)
}

if ($Apply) {
    $postgresId = (& docker compose ps -q postgres).Trim()
    if (-not $postgresId) {
        throw "PostgreSQL is not running. Start only postgres first; this script never starts the persistent worker."
    }
    $argsList += "--apply"
}

& docker @argsList
if ($LASTEXITCODE -ne 0) {
    throw "Contact ingestion exited with code $LASTEXITCODE"
}
