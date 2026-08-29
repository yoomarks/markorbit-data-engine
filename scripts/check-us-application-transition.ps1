param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest,
    [switch]$VerifySourceFiles,
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
            throw "$service must be running before the US Application transition gate."
        }
    }

    $persistentWorkerId = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($persistentWorkerId) {
        throw "Persistent worker is running. Stop it before the US Application transition gate."
    }

    $gateArgs = @(
        "-m", "app.us.application_transition_host_protocol",
        "--expected-history-parts", "$ExpectedHistoryParts"
    )
    if ($DeepSourceTest) {
        $gateArgs += "--deep-source-test"
    }
    if ($VerifySourceFiles) {
        $gateArgs += "--verify-source-files"
    }

    # The Python host protocol executes the transition exactly once. It emits a
    # small flat decision summary for Windows PowerShell control flow plus the
    # complete nested evidence as an opaque JSON line. PowerShell must never
    # deserialize the full nested evidence object.
    $protocolLines = @(& docker compose run --rm --no-deps -T `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @gateArgs)
    $exitCode = $LASTEXITCODE

    $summaryPrefix = "MARKORBIT_US_APPLICATION_TRANSITION_SUMMARY`t"
    $evidencePrefix = "MARKORBIT_US_APPLICATION_TRANSITION_EVIDENCE`t"
    $summaryLines = @($protocolLines | Where-Object {
        $_ -is [string] -and $_.StartsWith($summaryPrefix)
    })
    $evidenceLines = @($protocolLines | Where-Object {
        $_ -is [string] -and $_.StartsWith($evidencePrefix)
    })

    if ($summaryLines.Count -ne 1) {
        throw "US Application transition host protocol produced $($summaryLines.Count) summary lines; expected exactly 1."
    }
    if ($evidenceLines.Count -ne 1) {
        throw "US Application transition host protocol produced $($evidenceLines.Count) evidence lines; expected exactly 1."
    }

    $summaryJson = $summaryLines[0].Substring($summaryPrefix.Length)
    $evidenceJson = $evidenceLines[0].Substring($evidencePrefix.Length)
    if (-not $summaryJson.Trim()) {
        throw "US Application transition host protocol produced an empty summary."
    }
    if (-not $evidenceJson.Trim()) {
        throw "US Application transition host protocol produced empty full evidence."
    }

    try {
        $report = $summaryJson | ConvertFrom-Json
    }
    catch {
        throw "US Application transition host summary produced invalid JSON: $($_.Exception.Message)"
    }

    if ($report.host_protocol_version -ne "US_APPLICATION_TRANSITION_HOST_V1") {
        throw "Unexpected US Application transition host protocol version: $($report.host_protocol_version)"
    }
    if ($report.transition_version -ne "CN_TO_US_APPLICATION_TRANSITION_V2") {
        throw "Unexpected US Application transition version: $($report.transition_version)"
    }
    if ([int]$report.expected_history_parts -ne $ExpectedHistoryParts) {
        throw "US Application transition summary history-part pin drifted."
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "cn_to_us_application_transition_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $evidenceJson | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "Transition status: $($report.status)"
    Write-Host "CN gate passed: $($report.cn_gate_passed)"
    Write-Host "US pipeline evaluated: $($report.us_pipeline_evaluated)"
    Write-Host "Safe to start US replay: $($report.safe_to_start_us_replay)"
    Write-Host "Report: $OutputPath"
    if ($report.us_pipeline_state) {
        Write-Host "US pipeline state: $($report.us_pipeline_state)"
    }
    if ($report.reason_codes -and $report.reason_codes.Count -gt 0) {
        Write-Host "Reasons: $($report.reason_codes -join ', ')"
    }
    if ($report.next_action_code) {
        Write-Host "Next action: $($report.next_action_code)"
    }

    if ($exitCode -ne 0) {
        throw "US Application transition gate did not pass: status=$($report.status)"
    }
}
finally {
    Pop-Location
}
