param(
    [Parameter(Mandatory = $true)]
    [string]$CnServingCheckpointPath,
    [string]$OutputPath = "",
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $CnServingCheckpointPath -PathType Leaf)) {
        throw "CN serving-state checkpoint not found: $CnServingCheckpointPath"
    }
    $resolvedServing = (Resolve-Path -LiteralPath $CnServingCheckpointPath).Path

    $pythonCommand = $null
    $pythonPrefix = @()
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $pythonCommand = $venvPython
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $pythonCommand = (Get-Command python).Source
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $pythonCommand = (Get-Command py).Source
        $pythonPrefix = @("-3")
    }
    else {
        throw "Python 3 is required for the M1.7 promotion evaluator."
    }

    $promotionArgs = @(
        "-m",
        "app.release_promotion",
        "--serving-state-report",
        $resolvedServing,
        "--require-ready"
    )
    if ($Compact) {
        $promotionArgs += "--compact"
    }

    # Persisted-evidence evaluation only. This operator performs no PostgreSQL or
    # ClickHouse query and never starts/restarts/recreates Docker or the worker.
    $invokeArgs = @($pythonPrefix) + $promotionArgs
    $jsonLines = & $pythonCommand @invokeArgs
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"

    if (-not $json.Trim()) {
        throw "M1.7 release promotion evaluator produced no JSON report."
    }

    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "M1.7 release promotion evaluator produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "platformization_m17_promotion_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "M1.7 promotion status: $($report.status)"
    Write-Host "Operator evidence: $($report.operator_evidence_id)"
    Write-Host "Operator evidence valid: $($report.operator_evidence_valid)"
    Write-Host "CN serving-state checkpoint: $resolvedServing"
    Write-Host "CN serving-state status: $($report.current_serving_state_status)"
    Write-Host "Current serving-state valid: $($report.current_serving_state_valid)"
    Write-Host "Release promotion allowed: $($report.release_promotion_allowed)"
    Write-Host "Fresh full-corpus validation claimed: $($report.fresh_full_corpus_validation_claimed)"
    Write-Host "Package replay/rescan required: $($report.package_replay_or_rescan_required)"
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0 -or -not $report.release_promotion_allowed) {
        $reasonCodes = @($report.reasons | ForEach-Object { $_.code })
        throw "M1.7 release promotion blocked: status=$($report.status); reasons=$($reasonCodes -join ', ')"
    }

    Write-Host "M1.7 promotion evidence passed. This operator does not change VERSION."
}
finally {
    Pop-Location
}
