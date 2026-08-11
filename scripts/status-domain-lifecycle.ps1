param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest,
    [switch]$VerifyUSSourceFiles,
    [switch]$VerifyAssignmentSources,
    [switch]$VerifyTTABSources,
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
            throw "$service must be running before domain lifecycle inspection."
        }
    }

    $persistentWorkerId = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($persistentWorkerId) {
        throw "Persistent worker is running. Stop it before taking a deterministic lifecycle snapshot."
    }

    $statusArgs = @(
        "-m", "app.domain_lifecycle",
        "--expected-history-parts", "$ExpectedHistoryParts"
    )
    if ($DeepSourceTest) { $statusArgs += "--deep-source-test" }
    if ($VerifyUSSourceFiles) { $statusArgs += "--verify-us-source-files" }
    if ($VerifyAssignmentSources) { $statusArgs += "--verify-assignment-sources" }
    if ($VerifyTTABSources) { $statusArgs += "--verify-ttab-sources" }
    if ($Compact) { $statusArgs += "--compact" }

    # Read-only lifecycle snapshot. The chained gates stop at the first incomplete
    # domain and never mutate or start the next phase.
    $jsonLines = & docker compose run --rm --no-deps -T `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @statusArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Domain lifecycle process failed before a report was returned."
    }
    $json = $jsonLines -join "`n"
    if (-not $json.Trim()) {
        throw "Domain lifecycle produced no JSON report."
    }
    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "Domain lifecycle produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "domain_lifecycle_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "Current phase: $($report.current_phase)"
    Write-Host "Lifecycle status: $($report.status)"
    Write-Host "Report: $OutputPath"
    foreach ($domain in @("cn", "us_application", "us_assignment", "us_ttab")) {
        $gate = $report.gates.$domain
        Write-Host "$domain`: status=$($gate.status); accepted=$($gate.accepted)"
    }
    if ($report.next_action -and $report.next_action.code) {
        Write-Host "Next action: $($report.next_action.code)"
        if ($report.next_action.description) {
            Write-Host $report.next_action.description
        }
    }

    Write-Host "Lifecycle inspection complete. No replay or mutation was started."
}
finally {
    Pop-Location
}
