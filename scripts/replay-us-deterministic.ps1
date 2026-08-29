param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest,
    [switch]$Apply,
    [switch]$All,
    [ValidateRange(1, 1000000)]
    [int]$MaxPackages = 1,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "replay-telemetry.ps1")

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before deterministic US replay."
}

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before deterministic US replay."
    }
}

if ($Apply) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "assert-domain-apply-gate.ps1") `
        -TargetDomain "US_APPLICATION" `
        -ExpectedApplicationHistoryParts $ExpectedHistoryParts
    if ($LASTEXITCODE -ne 0) {
        throw "US Application apply gate failed; replay was not started."
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$mode = if ($Apply) { "apply" } else { "dryrun" }
if (-not $OutputPath) {
    $OutputPath = Join-Path "reports" "us_deterministic_replay_${mode}_$timestamp.json"
}
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $repoRoot $OutputPath
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $OutputPath
if (-not $outputDirectory) {
    throw "US deterministic replay output path must have a parent directory."
}
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$outputFileName = [System.IO.Path]::GetFileName($OutputPath)
if (-not $outputFileName -or $outputFileName -notmatch "^[A-Za-z0-9_.-]+$") {
    throw "US deterministic replay output file name is invalid."
}
$summaryFileName = "$outputFileName.summary.json"
$summaryPath = Join-Path $outputDirectory $summaryFileName

# Build separately. Do not use docker compose run --build for report-producing commands:
# Docker Desktop may emit progress/lifecycle text on host output streams.
Write-Host "Building US replay worker image..."
& docker compose build worker | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Unable to build worker image for deterministic US replay."
}

$pythonArgs = @(
    "python", "-m", "app.us.replay_executor",
    "--expected-history-parts", "$ExpectedHistoryParts"
)
if ($DeepSourceTest) {
    $pythonArgs += "--deep-source-test"
}
if ($All) {
    $pythonArgs += "--all"
} else {
    $pythonArgs += @("--max-packages", "$MaxPackages")
}
if ($Apply) {
    $pythonArgs += "--apply"
}
$replayCommand = ($pythonArgs -join " ") + " > /replay-output/$outputFileName"
$summaryCommand = "python -m app.us.replay_summary /replay-output/$outputFileName /replay-output/$summaryFileName"
$shellCommand = "$replayCommand && $summaryCommand"

foreach ($path in @($OutputPath, $summaryPath)) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        Remove-Item -LiteralPath $path -Force
    }
}

$telemetry = $null
$telemetryStatus = "NOT_RECORDED"
$telemetryError = ""
if ($Apply) {
    try {
        $telemetry = Start-DataEngineReplayTelemetry `
            -Domain "US_APPLICATION" `
            -Jurisdiction "US" `
            -CommandName "replay-us-deterministic.ps1"
        $telemetryStatus = "COMMAND_RUNNING"
    }
    catch {
        Write-Warning "Replay telemetry start failed without blocking US replay: $($_.Exception.Message)"
    }
}

try {
    $outputDirectoryDocker = $outputDirectory.Replace('\', '/')
    $runArgs = @(
        "run", "--rm", "--no-deps", "-T",
        "--volume", "${outputDirectoryDocker}:/replay-output",
        "worker", "sh", "-lc", $shellCommand
    )
    & docker compose @runArgs | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Deterministic US replay process failed before a report was returned."
    }
    if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "Deterministic US replay produced no full report: $OutputPath"
    }
    if (-not (Test-Path -LiteralPath $summaryPath -PathType Leaf)) {
        throw "Deterministic US replay produced no compact summary: $summaryPath"
    }

    # Never pass the full replay evidence object through Windows PowerShell 5.1 ConvertFrom-Json.
    # Python validates the full JSON and emits this fixed-schema compact summary instead.
    $summaryJson = Get-Content -LiteralPath $summaryPath -Raw -Encoding UTF8
    if (-not $summaryJson.Trim()) {
        throw "Deterministic US replay compact summary is empty."
    }
    try {
        $report = $summaryJson | ConvertFrom-Json
    }
    catch {
        throw "Deterministic US replay compact summary contains invalid JSON."
    }

    Write-Host "US deterministic replay mode: $($report.mode)"
    Write-Host "Status: $($report.status)"
    Write-Host "Report: $OutputPath"
    Write-Host "Summary: $summaryPath"
    Write-Host "Processed this run: $($report.processed_count)"
    Write-Host "Full source preflights this run: $($report.source_preflight_runs)"
    if ($report.remaining_count -ne $null) {
        Write-Host "Remaining: $($report.remaining_count)"
    }
    if (-not $Apply -and $report.dry_run_ready) {
        Write-Host "Dry run only. Re-run with -Apply to process the next package, or -Apply -All for the full remaining plan."
    }
    if ($report.status -eq "COMPLETE") {
        Write-Host "Replay is complete. Run audit-us-real-data.ps1 for the normal lightweight acceptance audit; VerifySourceFiles/source re-hash remains optional."
    }

    if ($report.status -in @("BLOCKED", "FAILED", "BUSY")) {
        $reason = if ($report.error) { $report.error } elseif ($report.blockers) { $report.blockers -join ", " } else { $report.status }
        throw "Deterministic US replay stopped: $reason"
    }
    if (-not $Apply -and -not $report.dry_run_ready -and $report.status -ne "COMPLETE") {
        throw "Deterministic US replay dry run did not satisfy the READY gate."
    }
    if ($Apply -and $MaxPackages -eq 1 -and -not $All -and -not $report.apply_one_package_ok) {
        throw "Deterministic US replay did not satisfy the exactly-one-package apply gate."
    }
    if ($telemetry) {
        $telemetryStatus = [string]$report.status
    }
}
catch {
    if ($telemetry) {
        $telemetryStatus = "COMMAND_FAILED"
        $telemetryError = $_.Exception.Message
    }
    throw
}
finally {
    if ($telemetry) {
        Complete-DataEngineReplayTelemetry `
            -Context $telemetry `
            -Status $telemetryStatus `
            -ErrorMessage $telemetryError `
            -ReportPath $OutputPath
    }
}
