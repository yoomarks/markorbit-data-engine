param(
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
            throw "$service must be running before the M1.7 platformization runtime gate."
        }
    }

    $persistentWorkerId = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($persistentWorkerId) {
        throw "Persistent worker is running. Stop it before the M1.7 platformization runtime gate."
    }

    $gateArgs = @("-m", "app.platformization_runtime_gate")
    if ($Compact) {
        $gateArgs += "--compact"
    }

    # The gate is read-only. Mount the checked-out app code so both the M1.7
    # static contract and the existing CN final checkpoint use this exact branch.
    $jsonLines = & docker compose run --rm --no-deps -T `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @gateArgs
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"

    if (-not $json.Trim()) {
        throw "M1.7 platformization runtime gate produced no JSON report."
    }

    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "M1.7 platformization runtime gate produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "platformization_m17_runtime_gate_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "M1.7 platformization runtime gate: $($report.status)"
    Write-Host "Static code ready: $($report.static_code_ready)"
    Write-Host "CN runtime acceptance evaluated: $($report.runtime_acceptance_evaluated)"
    Write-Host "CN runtime acceptance passed: $($report.runtime_acceptance_passed)"
    Write-Host "Release promotion eligible: $($report.release_promotion_eligible)"
    Write-Host "Release promoted by this gate: $($report.release_promoted)"
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0) {
        $reasonCodes = @($report.reasons | ForEach-Object { $_.code })
        throw "M1.7 platformization runtime gate did not pass: status=$($report.status); reasons=$($reasonCodes -join ', ')"
    }

    if (-not $report.release_promotion_eligible) {
        throw "M1.7 platformization runtime gate exited successfully without release promotion eligibility."
    }

    Write-Host "M1.7 code + real CN runtime acceptance passed. VERSION remains unchanged by this gate."
}
finally {
    Pop-Location
}
