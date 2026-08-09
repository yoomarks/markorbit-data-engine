param(
    [int]$BatchSize = 5000,
    [int]$SamplePerRule = 12,
    [string]$AsOf = ""
)

$ErrorActionPreference = "Stop"

$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "Persistent worker is running. Stop it first: docker compose stop worker"
}

$reportDir = Join-Path $PSScriptRoot "..\reports"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$reportPath = Join-Path $reportDir "cn_case_status_inference_$stamp.json"

$dockerArgs = @(
    "compose", "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.cn.audit_case_status_inference",
    "--batch-size", "$BatchSize",
    "--sample-per-rule", "$SamplePerRule"
)
if ($AsOf) {
    $dockerArgs += @("--as-of", $AsOf)
}

Write-Host "Auditing empirical CN case-status inference rules..."
& docker @dockerArgs | Tee-Object -FilePath $reportPath
if ($LASTEXITCODE -ne 0) {
    throw "CN case-status inference audit failed."
}

Write-Host "Audit report: $reportPath"
Write-Host "Persistent worker remains stopped."
