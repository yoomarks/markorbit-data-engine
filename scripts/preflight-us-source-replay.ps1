param(
    [int]$ExpectedHistoryParts = 0,
    [switch]$DeepSourceTest,
    [switch]$AllowUnpinnedDiscovery,
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

    # Build separately from the report-producing container run. Do not use `docker compose run --build`:
    # Docker Desktop can emit build/progress text on host output streams, which must never become
    # part of the JSON evidence channel.
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
    if ($outputFileName -notmatch "^[A-Za-z0-9_.-]+$") {
        throw "US source preflight output file name contains unsupported characters."
    }

    $summaryFileName = "$outputFileName.summary.json"
    $summaryPath = Join-Path $outputDirectory $summaryFileName

    # Redirect the full Python report inside the disposable container to a bind-mounted file,
    # then parse that full JSON in Python and emit a compact fixed-schema summary. Windows
    # PowerShell 5.1 never converts the large evidence object itself.
    $pythonArgs = @("python", "-m", "app.us.source_preflight")
    if ($ExpectedHistoryParts -gt 0) {
        $pythonArgs += @("--expected-history-parts", "$ExpectedHistoryParts")
    }
    if ($DeepSourceTest) {
        $pythonArgs += "--deep-source-test"
    }
    $preflightCommand = ($pythonArgs -join " ") + " > /preflight-output/$outputFileName"
    $summaryCommand = "python -m app.us.preflight_summary /preflight-output/$outputFileName /preflight-output/$summaryFileName"
    $shellCommand = "$preflightCommand && $summaryCommand"

    foreach ($path in @($OutputPath, $summaryPath)) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Remove-Item -LiteralPath $path -Force
        }
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
    if (-not (Test-Path -LiteralPath $summaryPath -PathType Leaf)) {
        throw "US source replay preflight produced no compact summary file: $summaryPath"
    }

    # The full evidence JSON is intentionally not passed to ConvertFrom-Json. Windows
    # PowerShell 5.1 can reject large/complex valid JSON objects while Python json.loads succeeds.
    $summaryJson = Get-Content -LiteralPath $summaryPath -Raw -Encoding UTF8
    if (-not $summaryJson.Trim()) {
        throw "US source replay preflight produced an empty compact summary file."
    }
    try {
        $report = $summaryJson | ConvertFrom-Json
    }
    catch {
        throw "US source replay preflight compact summary contains invalid JSON."
    }

    Write-Host "US source preflight status: $($report.status)"
    Write-Host "Safe to replay: $($report.safe_to_replay)"
    Write-Host "Report: $OutputPath"
    Write-Host "Summary: $summaryPath"
    Write-Host "Physical sources: $($report.physical_source_count)"
    Write-Host "Semantic sources: $($report.semantic_source_count)"
    Write-Host "Historical sources: $($report.history_source_count)"
    Write-Host "Daily sources: $($report.daily_source_count)"
    Write-Host "Historical baseline end: $($report.historical_baseline_end)"
    Write-Host "Archive sources needing staging: $($report.archive_staging_required_count)"
    Write-Host "Hard issues: $($report.hard_issue_types -join ', ')"
    Write-Host "Not-ready reasons: $($report.not_ready_reasons -join ', ')"
    Write-Host "Warnings: $($report.warning_reasons -join ', ')"

    if ($AllowUnpinnedDiscovery) {
        if ($ExpectedHistoryParts -gt 0) {
            throw "-AllowUnpinnedDiscovery cannot be combined with -ExpectedHistoryParts."
        }
        if ($report.discovery_only_not_ready -ne $true) {
            throw "US source discovery did not produce the single accepted unpinned NOT_READY state."
        }
        if ([int]$report.history_source_count -lt 1) {
            throw "US source discovery found no historical packages."
        }
        Write-Host "US_SOURCE_DISCOVERY_PASS"
        return
    }

    if ($report.status -eq "FAIL") {
        throw "US source replay preflight failed: $($report.hard_issue_types -join ', ')"
    }
    if ($report.status -eq "NOT_READY") {
        throw "US source replay preflight is not ready: $($report.not_ready_reasons -join ', ')"
    }
    if ($report.pinned_pass -ne $true) {
        throw "US source replay preflight did not satisfy the pinned PASS gate."
    }
    Write-Host "US_SOURCE_PINNED_PASS"
}
finally {
    Pop-Location
}
