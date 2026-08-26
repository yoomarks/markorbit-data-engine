param(
    [string]$ExpectedFileName = "2023_5.zip",
    [string]$OutputPath = "",
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
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
        throw "Python 3 is required for the CN serving-state checkpoint."
    }

    $checkpointArgs = @(
        "-m",
        "app.cn.serving_state_checkpoint",
        "--expected-file-name",
        $ExpectedFileName
    )
    if ($Compact) {
        $checkpointArgs += "--compact"
    }

    # Local, read-only control/system-metadata checkpoint. It never manages
    # database or worker lifecycle; the configured target services must already
    # be reachable through the repository environment.
    $invokeArgs = @($pythonPrefix) + $checkpointArgs
    $jsonLines = & $pythonCommand @invokeArgs
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"

    if (-not $json.Trim()) {
        throw "CN serving-state checkpoint produced no JSON report."
    }

    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "CN serving-state checkpoint produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $safeFileName = $ExpectedFileName -replace '[^A-Za-z0-9._-]', '_'
        $OutputPath = Join-Path "reports" "cn_serving_state_${safeFileName}_$timestamp.json"
    }

    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "CN serving-state status: $($report.status)"
    Write-Host "Checkpoint version: $($report.checkpoint_version)"
    Write-Host "Expected package: $($report.expected_file_name)"
    Write-Host "Expected package SUCCESS: $($report.expected_package_success)"
    Write-Host "CN packages processing: $($report.processing_package_count)"
    Write-Host "Serving tables ready: $($report.core_tables_ready)"
    Write-Host "Goods schema exact: $($report.goods_schema_exact)"

    foreach ($disk in @($report.disks)) {
        $freePercent = "unknown"
        if ($null -ne $disk.free_ratio) {
            $freePercent = "{0:P1}" -f [double]$disk.free_ratio
        }
        Write-Host "Disk $($disk.name): free=$freePercent path=$($disk.path)"
    }

    $reasonCodes = @($report.reasons | ForEach-Object { $_.code })
    if ($reasonCodes.Count -gt 0) {
        Write-Host "Reason codes: $($reasonCodes -join ', ')"
    }
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0) {
        throw "CN serving-state checkpoint blocked: status=$($report.status); reasons=$($reasonCodes -join ', ')"
    }

    Write-Host "CN serving-state checkpoint completed read-only. No service lifecycle action was performed."
}
finally {
    Pop-Location
}
