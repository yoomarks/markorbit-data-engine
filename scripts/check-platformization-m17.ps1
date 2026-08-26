param(
    [string]$CnAcceptanceReceiptPath = "",
    [string]$CnServingCheckpointPath = "",
    [string]$ExpectedCnFileName = "2023_5.zip",
    [string]$OutputPath = "",
    [switch]$Compact,
    [switch]$UseDocker
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if ($CnAcceptanceReceiptPath -and $CnServingCheckpointPath) {
        throw "Specify exactly one CN runtime evidence path: -CnAcceptanceReceiptPath or -CnServingCheckpointPath."
    }

    $gateArgs = @(
        "-m",
        "app.platformization_runtime_gate",
        "--expected-cn-file-name",
        $ExpectedCnFileName
    )
    if ($Compact) {
        $gateArgs += "--compact"
    }

    $receiptDisplayPath = "<missing>"
    $servingDisplayPath = "<missing>"
    $resolvedReceipt = $null
    $resolvedServing = $null

    if ($CnAcceptanceReceiptPath) {
        if (-not (Test-Path -LiteralPath $CnAcceptanceReceiptPath -PathType Leaf)) {
            throw "CN acceptance receipt not found: $CnAcceptanceReceiptPath"
        }
        $resolvedReceipt = (Resolve-Path -LiteralPath $CnAcceptanceReceiptPath).Path
        $receiptDisplayPath = $resolvedReceipt
    }
    if ($CnServingCheckpointPath) {
        if (-not (Test-Path -LiteralPath $CnServingCheckpointPath -PathType Leaf)) {
            throw "CN serving-state checkpoint not found: $CnServingCheckpointPath"
        }
        $resolvedServing = (Resolve-Path -LiteralPath $CnServingCheckpointPath).Path
        $servingDisplayPath = $resolvedServing
    }

    if ($UseDocker) {
        $volumeArgs = @(
            "--volume",
            "${repoRoot}\app:/app/app:ro"
        )
        if ($resolvedReceipt) {
            $evidenceDirectory = Split-Path -Parent $resolvedReceipt
            $evidenceName = Split-Path -Leaf $resolvedReceipt
            $containerEvidencePath = "/evidence/$evidenceName"
            $volumeArgs += @(
                "--volume",
                "${evidenceDirectory}:/evidence:ro"
            )
            $gateArgs += @(
                "--cn-acceptance-receipt",
                $containerEvidencePath
            )
        }
        elseif ($resolvedServing) {
            $evidenceDirectory = Split-Path -Parent $resolvedServing
            $evidenceName = Split-Path -Leaf $resolvedServing
            $containerEvidencePath = "/evidence/$evidenceName"
            $volumeArgs += @(
                "--volume",
                "${evidenceDirectory}:/evidence:ro"
            )
            $gateArgs += @(
                "--cn-serving-checkpoint",
                $containerEvidencePath
            )
        }

        # Docker execution is explicit opt-in only. --no-deps prevents this gate
        # from starting PostgreSQL or ClickHouse as dependencies.
        $composeArgs = @("compose", "run", "--rm", "--no-deps", "-T") +
            $volumeArgs + @("worker", "python") + $gateArgs
        $jsonLines = & docker @composeArgs
    }
    else {
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
            throw (
                "Python 3 is required for the default local M1.7 gate. " +
                "Docker is not started automatically; use -UseDocker only when explicitly desired."
            )
        }

        if ($resolvedReceipt) {
            $gateArgs += @(
                "--cn-acceptance-receipt",
                $resolvedReceipt
            )
        }
        elseif ($resolvedServing) {
            $gateArgs += @(
                "--cn-serving-checkpoint",
                $resolvedServing
            )
        }
        $invokeArgs = @($pythonPrefix) + $gateArgs
        $jsonLines = & $pythonCommand @invokeArgs
    }

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
    Write-Host "Execution mode: $(if ($UseDocker) { 'DOCKER_EXPLICIT' } else { 'LOCAL_PYTHON' })"
    Write-Host "Runtime evidence mode: $($report.runtime_evidence_mode)"
    Write-Host "CN acceptance receipt: $receiptDisplayPath"
    Write-Host "CN serving-state checkpoint: $servingDisplayPath"
    Write-Host "Expected CN package: $ExpectedCnFileName"
    Write-Host "Static code ready: $($report.static_code_ready)"
    Write-Host "CN runtime evidence evaluated: $($report.runtime_acceptance_evaluated)"
    Write-Host "CN runtime evidence passed: $($report.runtime_acceptance_passed)"
    Write-Host "Promotion basis: $($report.promotion_basis)"
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

    Write-Host "M1.7 code + persisted CN runtime evidence passed. VERSION remains unchanged by this gate."
}
finally {
    Pop-Location
}
