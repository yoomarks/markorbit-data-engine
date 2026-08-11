param(
    [string]$ManifestRelativePath = "manifests/us_ttab/corpus.json",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Docker Compose worker state." }
if ($worker) { throw "Persistent worker is running. Stop it before TTAB corpus audit." }

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before TTAB corpus audit."
    }
}

$manifest = "/data/raw/" + ($ManifestRelativePath -replace '\\', '/')
$jsonLines = & docker compose run --build --rm --no-deps -T worker python -m app.us_ttab.corpus_audit --manifest $manifest
$exitCode = $LASTEXITCODE
$json = $jsonLines -join "`n"
if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "us_ttab_corpus_acceptance_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) { New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null }
$json | Set-Content -Encoding UTF8 $OutputPath
Write-Host $json
Write-Host "Report: $OutputPath"
if ($exitCode -ne 0) { throw "US TTAB corpus acceptance is not PASS/PASS_WITH_WARNINGS. See the report above." }
