param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("US_APPLICATION", "US_ASSIGNMENT", "US_TTAB")]
    [string]$TargetDomain,

    [ValidateRange(0, 9999)]
    [int]$ExpectedApplicationHistoryParts = 0
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    Write-Host "Running mandatory storage headroom gate before $TargetDomain mutation..."
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "assert-storage-headroom.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Storage headroom gate blocked mutation for $TargetDomain."
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    switch ($TargetDomain) {
        "US_APPLICATION" {
            $gateScript = Join-Path $PSScriptRoot "check-cn-final-checkpoint.ps1"
            $gateReport = Join-Path "reports" "apply_gate_cn_to_us_application_$timestamp.json"
            $gateArgs = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $gateScript,
                "-OutputPath", $gateReport,
                "-Compact"
            )
        }
        "US_ASSIGNMENT" {
            if ($ExpectedApplicationHistoryParts -lt 1) {
                throw "ExpectedApplicationHistoryParts is required for the US Assignment apply gate."
            }
            $gateScript = Join-Path $PSScriptRoot "check-us-assignment-transition.ps1"
            $gateReport = Join-Path "reports" "apply_gate_us_application_to_assignment_$timestamp.json"
            $gateArgs = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $gateScript,
                "-ExpectedHistoryParts", "$ExpectedApplicationHistoryParts",
                "-OutputPath", $gateReport,
                "-Compact"
            )
        }
        "US_TTAB" {
            if ($ExpectedApplicationHistoryParts -lt 1) {
                throw "ExpectedApplicationHistoryParts is required for the US TTAB apply gate."
            }
            $gateScript = Join-Path $PSScriptRoot "check-us-ttab-transition.ps1"
            $gateReport = Join-Path "reports" "apply_gate_us_assignment_to_ttab_$timestamp.json"
            $gateArgs = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $gateScript,
                "-ExpectedHistoryParts", "$ExpectedApplicationHistoryParts",
                "-OutputPath", $gateReport,
                "-Compact"
            )
        }
    }

    Write-Host "Running mandatory apply transition gate for $TargetDomain..."
    & powershell.exe @gateArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Apply transition gate blocked mutation for $TargetDomain."
    }
    Write-Host "Apply transition gate passed for $TargetDomain. Report: $gateReport"
}
finally {
    Pop-Location
}
