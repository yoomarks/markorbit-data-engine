param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest,
    [switch]$VerifySourceFiles,
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
            throw "$service must be running before the US Application transition gate."
        }
    }

    $persistentWorkerId = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($persistentWorkerId) {
        throw "Persistent worker is running. Stop it before the US Application transition gate."
    }

    $gateArgs = @(
        "-m", "app.us.application_transition_gate",
        "--expected-history-parts", "$ExpectedHistoryParts"
    )
    if ($DeepSourceTest) {
        $gateArgs += "--deep-source-test"
    }
    if ($VerifySourceFiles) {
        $gateArgs += "--verify-source-files"
    }
    if ($Compact) {
        $gateArgs += "--compact"
    }

    # Entire transition gate is read-only. It first runs the CN final checkpoint
    # and does not evaluate the US pipeline at all unless CN is accepted.
    $jsonLines = & docker compose run --rm --no-deps -T `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @gateArgs
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"

    if (-not $json.Trim()) {
        throw "US Application transition gate produced no JSON report."
    }
    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "US Application transition gate produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "cn_to_us_application_transition_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "Transition status: $($report.status)"
    Write-Host "CN gate passed: $($report.cn_gate_passed)"
    Write-Host "US pipeline evaluated: $($report.us_pipeline_evaluated)"
    Write-Host "Safe to start US replay: $($report.safe_to_start_us_replay)"
    Write-Host "Report: $OutputPath"
    if ($report.us_pipeline_state) {
        Write-Host "US pipeline state: $($report.us_pipeline_state)"
    }
    if ($report.reason_codes -and $report.reason_codes.Count -gt 0) {
        Write-Host "Reasons: $($report.reason_codes -join ', ')"
    }
    if ($report.next_action -and $report.next_action.code) {
        Write-Host "Next action: $($report.next_action.code)"
    }

    if ($exitCode -ne 0) {
        throw "US Application transition gate did not pass: status=$($report.status)"
    }
}
finally {
    Pop-Location
}
