param(
    [string]$CnAcceptanceReceiptPath = "",
    [string]$ExpectedCnFileName = "2023_5.zip",
    [string]$OutputPath = "",
    [switch]$Compact,
    [switch]$UseDocker
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
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
    $resolvedReceipt = $null
    if ($CnAcceptanceReceiptPath) {
        if (-not (Test-Path -LiteralPath $CnAcceptanceReceiptPath -PathType Leaf)) {
            throw "CN acceptance receipt not found: $CnAcceptanceReceiptPath"
        }
        $resolvedReceipt = (Resolve-Path -LiteralPath $CnAcceptanceReceiptPath).Path
        $receiptDisplayPath = $resolvedReceipt
    }

    if ($UseDocker) {
        $volumeArgs = @(
            "--volume",
            "${repoRoot}\app:/app/app:ro"
        )
        if ($resolvedReceipt) {
            $receiptDirectory = Split-Path -Parent $resolvedReceipt
            $receiptName = Split-Path -Leaf $resolvedReceipt
            $containerReceiptPath = "/evidence/$receiptName"
            $volumeArgs += @(
                "--volume",
                "${receiptDirectory}:/evidence:ro"
            )
            $gateArgs += @(
                "--cn-acceptance-receipt",
                $containerReceiptPath
            )
        }

        # Docker execution is opt-in only. --no-deps prevents this gate from
        # starting PostgreSQL or ClickHouse as dependencies.
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
    Write-Host "Receipt-driven: $($report.receipt_driven)"
    Write-Host "Execution mode: $(if ($UseDocker) { 'DOCKER_EXPLICIT' } else { 'LOCAL_PYTHON' })"
    Write-Host "CN acceptance receipt: $receiptDisplayPath"
    Write-Host "Expected CN package: $ExpectedCnFileName"
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

    Write-Host "M1.7 code + persisted real CN runtime acceptance passed. VERSION remains unchanged by this gate."
}
finally {
    Pop-Location
}
