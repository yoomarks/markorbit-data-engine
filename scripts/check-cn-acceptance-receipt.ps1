param(
    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,
    [string]$ExpectedFileName = "2023_5.zip",
    [string]$OutputPath = "",
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
        throw "CN acceptance receipt not found: $ReceiptPath"
    }
    $resolvedReceipt = (Resolve-Path -LiteralPath $ReceiptPath).Path

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
        throw "Python 3 is required for local receipt validation. Docker is intentionally not used by this script."
    }

    $argsList = @(
        @($pythonPrefix) +
        @(
            "-m",
            "app.cn.acceptance_receipt",
            $resolvedReceipt,
            "--expected-file-name",
            $ExpectedFileName
        )
    )
    if ($Compact) {
        $argsList += "--compact"
    }

    $jsonLines = & $pythonCommand @argsList
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"
    if (-not $json.Trim()) {
        throw "CN acceptance receipt validator produced no JSON report."
    }

    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "CN acceptance receipt validator produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "cn_acceptance_receipt_check_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "CN acceptance receipt check: $($report.status)"
    Write-Host "Accepted: $($report.accepted)"
    Write-Host "Expected package: $ExpectedFileName"
    Write-Host "Receipt package: $($report.receipt_file_name)"
    Write-Host "Readiness: $($report.readiness_status)"
    Write-Host "Final checkpoint executed: $($report.final_checkpoint_executed)"
    Write-Host "Next mode: $($report.next_mode)"
    Write-Host "Docker required: $($report.docker_required)"
    Write-Host "Database connection required: $($report.database_connection_required)"
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0 -or $report.accepted -ne $true) {
        $reasonCodes = @($report.reasons | ForEach-Object { $_.code })
        throw "CN acceptance receipt is not accepted: status=$($report.status); reasons=$($reasonCodes -join ', ')"
    }
}
finally {
    Pop-Location
}
