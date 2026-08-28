param(
    [int]$ExpectedHistoryParts = 0,
    [switch]$DeepSourceTest,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $worker = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($worker) {
        throw "Persistent worker is running. Stop it before US source replay preflight."
    }

    # Build separately from the report-producing container run. Docker Desktop can emit
    # progress/status text on host output streams; never use those streams as JSON evidence.
    Write-Host "Building US preflight worker image..."
    & docker compose build worker | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to build worker image for US source replay preflight."
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "us_source_preflight_$timestamp.json"
    }
    if (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
        $OutputPath = Join-Path $repoRoot $OutputPath
    }
    $OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
    $outputDirectory = Split-Path -Parent $OutputPath
    if (-not $outputDirectory) {
        throw "US source preflight output path must have a parent directory."
    }
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    $outputFileName = [System.IO.Path]::GetFileName($OutputPath)
    if (-not $outputFileName) {
        throw "US source preflight output path must have a file name."
    }

    # Redirect Python stdout *inside* the disposable container to a bind-mounted file.
    # This keeps Docker/Compose host progress, warnings, and container lifecycle output
    # completely outside the JSON evidence channel.
    $pythonArgs = @("python", "-m", "app.us.source_preflight")
    if ($ExpectedHistoryParts -gt 0) {
        $pythonArgs += @("--expected-history-parts", "$ExpectedHistoryParts")
    }
    if ($DeepSourceTest) {
        $pythonArgs += "--deep-source-test"
    }
    $shellCommand = (($pythonArgs | ForEach-Object {
        if ($_ -match "^[A-Za-z0-9_./:-]+$") { $_ } else { "'" + ($_ -replace "'", "'\"'\"'") + "'" }
    }) -join " ") + " > /preflight-output/$outputFileName"

    if (Test-Path -LiteralPath $OutputPath -PathType Leaf) {
        Remove-Item -LiteralPath $OutputPath -Force
    }

    $runArgs = @(
        "run", "--rm", "--no-deps", "-T",
        "--volume", "${outputDirectory}:/preflight-output",
        "worker", "sh", "-lc", $shellCommand
    )
    & docker compose @runArgs | Out-Host
    $runExitCode = $LASTEXITCODE
    if ($runExitCode -ne 0) {
        throw "US source replay preflight process failed with exit code $runExitCode."
    }
    if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "US source replay preflight produced no report file: $OutputPath"
    }

    $json = Get-Content -LiteralPath $OutputPath -Raw -Encoding UTF8
    if (-not $json.Trim()) {
        throw "US source replay preflight produced an empty report file."
    }
    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        $preview = if ($json.Length -gt 500) { $json.Substring(0, 500) } else { $json }
        throw "US source replay preflight report file contains invalid JSON. Preview: $preview"
    }

    Write-Host "US source preflight status: $($report.status)"
    Write-Host "Safe to replay: $($report.safe_to_replay)"
    Write-Host "Report: $OutputPath"
    Write-Host "Historical sources: $($report.source_inventory.history_source_count)"
    Write-Host "Daily sources: $($report.source_inventory.daily_source_count)"
    Write-Host "Historical baseline end: $($report.historical_baseline_end)"
    Write-Host "Archive sources needing staging: $($report.archive_staging_required_count)"

    if ($report.status -eq "FAIL") {
        throw "US source replay preflight failed: $($report.hard_issue_types -join ', ')"
    }
    if ($report.status -eq "NOT_READY") {
        throw "US source replay preflight is not ready: $($report.not_ready_reasons -join ', ')"
    }
}
finally {
    Pop-Location
}
