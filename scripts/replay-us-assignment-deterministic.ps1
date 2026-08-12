param(
    [string]$ManifestRelativePath = "manifests/us_assignment/corpus.json",
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedApplicationHistoryParts,
    [switch]$Apply,
    [switch]$All,
    [switch]$ResumeFailed,
    [ValidateRange(1, 1000000)][int]$MaxPackages = 1,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "replay-telemetry.ps1")

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Docker Compose worker state." }
if ($worker) { throw "Persistent worker is running. Stop it before deterministic Assignment replay." }

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before deterministic Assignment replay."
    }
}

if ($Apply) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "assert-domain-apply-gate.ps1") `
        -TargetDomain "US_ASSIGNMENT" `
        -ExpectedApplicationHistoryParts $ExpectedApplicationHistoryParts
    if ($LASTEXITCODE -ne 0) {
        throw "US Assignment apply gate failed; replay was not started."
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$mode = if ($Apply) { "apply" } else { "dryrun" }
if (-not $OutputPath) {
    $OutputPath = Join-Path "reports" "us_assignment_replay_${mode}_$timestamp.json"
}

$telemetry = $null
$telemetryStatus = "NOT_RECORDED"
$telemetryError = ""
if ($Apply) {
    try {
        $telemetry = Start-DataEngineReplayTelemetry `
            -Domain "US_ASSIGNMENT" `
            -Jurisdiction "US_ASSIGNMENT" `
            -CommandName "replay-us-assignment-deterministic.ps1"
        $telemetryStatus = "COMMAND_RUNNING"
    }
    catch {
        Write-Warning "Replay telemetry start failed without blocking Assignment replay: $($_.Exception.Message)"
    }
}

try {
    if ($Apply) {
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "apply-us-assignment-schema.ps1")
        if ($LASTEXITCODE -ne 0) { throw "US Assignment schema gate failed." }
    }

    $manifest = "/data/raw/" + ($ManifestRelativePath -replace '\\', '/')
    $args = @(
        "run", "--build", "--rm", "--no-deps", "-T", "worker",
        "python", "-m", "app.us_assignment.corpus_replay",
        "--manifest", $manifest,
        "--max-packages", "$MaxPackages"
    )
    if ($Apply) { $args += "--apply" }
    if ($All) { $args += "--all" }
    if ($ResumeFailed) { $args += "--resume-failed" }

    $jsonLines = & docker compose @args
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"
    if (-not $OutputPath) {
        throw "US Assignment replay output path was not resolved."
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) { New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null }
    $json | Set-Content -Encoding UTF8 $OutputPath
    Write-Host $json
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0) { throw "Deterministic US Assignment replay failed. See the JSON report above." }
    $report = $json | ConvertFrom-Json
    if ($report.status -eq "RETRY_REQUIRED" -and -not $ResumeFailed) {
        throw "US Assignment replay requires explicit -ResumeFailed before the failed package can be retried."
    }
    if ($report.status -in @("BLOCKED", "FAILED", "BUSY")) {
        throw "Deterministic US Assignment replay stopped: $($report.status)"
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
