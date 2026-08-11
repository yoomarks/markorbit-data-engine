param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedApplicationHistoryParts,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$ExpectedApplicationDailyThrough,

    [switch]$VerifyApplicationSourceFiles,

    [string]$AssignmentManifestRelativePath = "manifests/us_assignment/corpus.json",
    [string]$TtabManifestRelativePath = "manifests/us_ttab/corpus.json",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $persistentWorkerId = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($persistentWorkerId) {
        throw "Persistent worker is running. Stop it before final acceptance."
    }
    foreach ($service in @("postgres", "clickhouse")) {
        $running = docker compose ps --status running -q $service
        if ($LASTEXITCODE -ne 0 -or -not $running) {
            throw "$service must be running before final acceptance."
        }
    }

    if (-not $OutputRoot) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputRoot = Join-Path "reports" "four_domain_final_$timestamp"
    }
    New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

    $lifecyclePath = Join-Path $OutputRoot "00_domain_lifecycle.json"
    $cnPath = Join-Path $OutputRoot "01_cn_acceptance.json"
    $applicationPath = Join-Path $OutputRoot "02_us_application_acceptance.json"
    $assignmentPath = Join-Path $OutputRoot "03_us_assignment_manifest_acceptance.json"
    $ttabPath = Join-Path $OutputRoot "04_us_ttab_manifest_acceptance.json"
    $finalPath = Join-Path $OutputRoot "05_four_domain_acceptance.json"
    $manifestPath = Join-Path $OutputRoot "run_manifest.json"

    Write-Host "Checking frozen domain lifecycle before generating formal reports..."
    & (Join-Path $PSScriptRoot "status-domain-lifecycle.ps1") `
        -ExpectedHistoryParts $ExpectedApplicationHistoryParts `
        -OutputPath $lifecyclePath
    if ($LASTEXITCODE -ne 0) {
        throw "Domain lifecycle inspection failed."
    }

    $lifecycle = Get-Content -LiteralPath $lifecyclePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($lifecycle.current_phase -ne "FINAL_ACCEPTANCE" -or $lifecycle.status -ne "FINAL_ACCEPTANCE_REQUIRED") {
        $nextAction = if ($lifecycle.next_action -and $lifecycle.next_action.code) {
            $lifecycle.next_action.code
        } else {
            "UNKNOWN"
        }
        throw "Four-domain final acceptance is not unlocked: phase=$($lifecycle.current_phase); status=$($lifecycle.status); next_action=$nextAction"
    }

    Write-Host "Generating formal CN acceptance report..."
    & (Join-Path $PSScriptRoot "audit-m16-acceptance.ps1") -OutputPath $cnPath
    if ($LASTEXITCODE -ne 0) { throw "CN formal acceptance report failed." }

    Write-Host "Generating formal US Application acceptance report..."
    $applicationArgs = @{
        ExpectedHistoryParts = $ExpectedApplicationHistoryParts
        OutputPath = $applicationPath
    }
    if ($VerifyApplicationSourceFiles) {
        $applicationArgs["VerifySourceFiles"] = $true
    }
    & (Join-Path $PSScriptRoot "audit-us-real-data.ps1") @applicationArgs
    if ($LASTEXITCODE -ne 0) { throw "US Application formal acceptance report failed." }

    Write-Host "Generating formal US Assignment manifest acceptance report..."
    & (Join-Path $PSScriptRoot "audit-us-assignment-corpus.ps1") `
        -ManifestRelativePath $AssignmentManifestRelativePath `
        -OutputPath $assignmentPath
    if ($LASTEXITCODE -ne 0) { throw "US Assignment formal acceptance report failed." }

    Write-Host "Generating formal US TTAB manifest acceptance report..."
    & (Join-Path $PSScriptRoot "audit-us-ttab-corpus.ps1") `
        -ManifestRelativePath $TtabManifestRelativePath `
        -OutputPath $ttabPath
    if ($LASTEXITCODE -ne 0) { throw "US TTAB formal acceptance report failed." }

    Write-Host "Running existing MARKORBIT_FOUR_DOMAIN_ACCEPTANCE_V1 gate..."
    & (Join-Path $PSScriptRoot "audit-four-domain-acceptance.ps1") `
        -CnReport $cnPath `
        -ApplicationReport $applicationPath `
        -AssignmentReport $assignmentPath `
        -TtabReport $ttabPath `
        -ExpectedApplicationHistoryParts $ExpectedApplicationHistoryParts `
        -ExpectedApplicationDailyThrough $ExpectedApplicationDailyThrough `
        -OutputPath $finalPath
    if ($LASTEXITCODE -ne 0) { throw "Formal four-domain acceptance failed." }

    $finalReport = Get-Content -LiteralPath $finalPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $gitHead = ""
    try {
        $gitHead = (git rev-parse HEAD 2>$null).Trim()
    }
    catch {
        $gitHead = ""
    }
    $artifactPaths = [ordered]@{
        lifecycle = $lifecyclePath
        cn = $cnPath
        application = $applicationPath
        assignment = $assignmentPath
        ttab = $ttabPath
        final = $finalPath
    }
    $artifacts = [ordered]@{}
    foreach ($name in $artifactPaths.Keys) {
        $path = $artifactPaths[$name]
        $artifacts[$name] = [ordered]@{
            path = $path
            sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $runManifest = [ordered]@{
        run_version = "MARKORBIT_FOUR_DOMAIN_FINAL_RUN_V1"
        created_at = (Get-Date).ToString("o")
        git_head = $gitHead
        policy = [ordered]@{
            expected_application_history_parts = $ExpectedApplicationHistoryParts
            expected_application_daily_through = $ExpectedApplicationDailyThrough
            assignment_manifest_relative_path = $AssignmentManifestRelativePath
            ttab_manifest_relative_path = $TtabManifestRelativePath
            verify_application_source_files = [bool]$VerifyApplicationSourceFiles
        }
        lifecycle_status = $lifecycle.status
        final_status = $finalReport.status
        artifacts = $artifacts
    }
    $runManifest | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 $manifestPath

    Write-Host "Final four-domain acceptance status: $($finalReport.status)"
    Write-Host "Final report: $finalPath"
    Write-Host "Run manifest: $manifestPath"
    Write-Host "All intermediate formal reports were retained under: $OutputRoot"
}
finally {
    Pop-Location
}
