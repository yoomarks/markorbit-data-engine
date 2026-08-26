param(
    [string]$CnAcceptanceReceiptPath = "",
    [string]$ExpectedCnFileName = "2023_5.zip",
    [string]$OutputPath = "",
    [switch]$Compact
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

    $volumeArgs = @(
        "--volume",
        "${repoRoot}\app:/app/app:ro"
    )
    $receiptDisplayPath = "<missing>"
    if ($CnAcceptanceReceiptPath) {
        if (-not (Test-Path -LiteralPath $CnAcceptanceReceiptPath -PathType Leaf)) {
            throw "CN acceptance receipt not found: $CnAcceptanceReceiptPath"
        }
        $receiptPath = (Resolve-Path -LiteralPath $CnAcceptanceReceiptPath).Path
        $receiptDirectory = Split-Path -Parent $receiptPath
        $receiptName = Split-Path -Leaf $receiptPath
        $containerReceiptPath = "/evidence/$receiptName"
        $volumeArgs += @(
            "--volume",
            "${receiptDirectory}:/evidence:ro"
        )
        $gateArgs += @(
            "--cn-acceptance-receipt",
            $containerReceiptPath
        )
        $receiptDisplayPath = $receiptPath
    }

    # Receipt-driven and read-only: the gate only reads static code plus the
    # persisted CN post-import report. It never connects to PostgreSQL/ClickHouse,
    # reruns CN replay/readiness, or executes the CN final checkpoint.
    $jsonLines = & docker compose run --rm --no-deps -T `
        @volumeArgs `
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
    Write-Host "Receipt-driven: $($report.receipt_driven)"
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
