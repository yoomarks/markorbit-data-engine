param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest,
    [switch]$VerifyUSSourceFiles,
    [switch]$VerifyAssignmentSources,
    [string]$OutputPath = "",
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    foreach ($service in @("postgres", "clickhouse")) {
        $running = docker compose ps --status running -q $service
        if ($LASTEXITCODE -ne 0 -or -not $running) {
            throw "$service must be running before the US Assignment transition gate."
        }
    }

    $persistentWorkerId = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($persistentWorkerId) {
        throw "Persistent worker is running. Stop it before the US Assignment transition gate."
    }

    $gateArgs = @(
        "-m", "app.us_assignment.transition_gate",
        "--expected-history-parts", "$ExpectedHistoryParts"
    )
    if ($DeepSourceTest) { $gateArgs += "--deep-source-test" }
    if ($VerifyUSSourceFiles) { $gateArgs += "--verify-us-source-files" }
    if ($VerifyAssignmentSources) { $gateArgs += "--verify-assignment-sources" }
    if ($Compact) { $gateArgs += "--compact" }

    # Read-only chained gate: CN final acceptance -> US Application acceptance
    # -> Assignment readiness. Assignment is never inspected before Application acceptance.
    $jsonLines = & docker compose run --rm --no-deps -T `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @gateArgs
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"

    if (-not $json.Trim()) {
        throw "US Assignment transition gate produced no JSON report."
    }
    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "US Assignment transition gate produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "us_application_to_assignment_transition_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "Transition status: $($report.status)"
    Write-Host "Application gate passed: $($report.application_gate_passed)"
    Write-Host "Assignment phase unlocked: $($report.ready_for_assignment_phase)"
    Write-Host "Assignment readiness evaluated: $($report.assignment_readiness_evaluated)"
    Write-Host "Assignment accepted/ready: $($report.assignment_ready)"
    Write-Host "Report: $OutputPath"
    if ($report.assignment_state) {
        Write-Host "Assignment state: $($report.assignment_state)"
    }
    if ($report.reason_codes -and $report.reason_codes.Count -gt 0) {
        Write-Host "Reasons: $($report.reason_codes -join ', ')"
    }
    if ($report.next_action -and $report.next_action.code) {
        Write-Host "Next action: $($report.next_action.code)"
    }

    if ($exitCode -ne 0) {
        throw "US Assignment transition gate did not unlock: status=$($report.status)"
    }
}
finally {
    Pop-Location
}
