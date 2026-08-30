param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$Apply,
    [ValidateRange(1, 1000000)]
    [int]$ExpectedSequence = 1,
    [string]$ExpectedFileName = "apc18840407-20251231-01.zip",
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedPackageSha256 = "9b65bdcb80c2bdd6efa6869432771c30613bed6dc8efd3d4589e2fd8b334b062",
    [ValidateRange(0, 1000000)]
    [int]$ExpectedRemainingBefore = 310,
    [ValidateRange(0, 1000000)]
    [int]$ExpectedRemainingAfter = 309,
    [string]$EvidenceRoot = "reports"
)

$ErrorActionPreference = "Stop"
if (-not $Apply) {
    throw "Bounded US capacity pilot mutates US Application state. Re-run with explicit -Apply after reviewing the dry-run plan."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $expectedPackageSha = $ExpectedPackageSha256.Trim().ToLowerInvariant()
    $engineSha = (git rev-parse HEAD).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $engineSha -notmatch '^[0-9a-f]{40}$') {
        throw "Unable to resolve exact Data Engine HEAD SHA."
    }
    if (git status --porcelain) {
        throw "Working tree must be clean before the bounded US capacity pilot."
    }

    $worker = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($worker) {
        throw "Persistent worker is running. Do not run the bounded US capacity pilot concurrently."
    }
    foreach ($service in @("postgres", "clickhouse")) {
        $running = docker compose ps --status running -q $service
        if ($LASTEXITCODE -ne 0 -or -not $running) {
            throw "$service must be running before the bounded US capacity pilot."
        }
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $evidenceDir = Join-Path $EvidenceRoot "us_capacity_pilot_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    $dryRunPath = Join-Path $evidenceDir "dry_run.json"
    $dryRunSummaryPath = "$dryRunPath.summary.json"
    $beforeProfilePath = Join-Path $evidenceDir "storage_before.json"
    $replayPath = Join-Path $evidenceDir "replay_one_package.json"
    $replaySummaryPath = "$replayPath.summary.json"
    $afterProfilePath = Join-Path $evidenceDir "storage_after.json"
    $receiptPath = Join-Path $evidenceDir "pilot_receipt.json"

    Write-Host "===== US CAPACITY PILOT DRY RUN ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "replay-us-deterministic.ps1") `
        -ExpectedHistoryParts $ExpectedHistoryParts `
        -MaxPackages 1 `
        -OutputPath $dryRunPath
    if ($LASTEXITCODE -ne 0) {
        throw "US deterministic dry run failed. No pilot mutation was started."
    }
    if (-not (Test-Path -LiteralPath $dryRunSummaryPath -PathType Leaf)) {
        throw "US deterministic dry run produced no compact summary. No pilot mutation was started."
    }
    $dryRun = Get-Content -LiteralPath $dryRunSummaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $dryRun.dry_run_ready -or -not $dryRun.next_step) {
        throw "US dry run is not READY for exactly one bounded pilot package."
    }
    if ([int]$dryRun.processed_count -ne 0 -or [int]$dryRun.remaining_count -ne $ExpectedRemainingBefore) {
        throw "US dry-run corpus position drifted from the frozen pilot baseline."
    }
    if ([int]$dryRun.next_step.sequence -ne $ExpectedSequence -or `
        [string]$dryRun.next_step.file_name -ne $ExpectedFileName -or `
        ([string]$dryRun.next_step.sha256).ToLowerInvariant() -ne $expectedPackageSha) {
        throw "US dry-run next package identity drifted from the frozen exactly-one-package pilot."
    }
    Write-Host "FROZEN_US_PILOT_PACKAGE_IDENTITY_OK"
    Write-Host "Next package: $($dryRun.next_step.file_name)"
    Write-Host "Sequence: $($dryRun.next_step.sequence)"
    Write-Host "SHA256: $($dryRun.next_step.sha256)"
    Write-Host "Remaining before: $($dryRun.remaining_count)"

    Write-Host "`n===== CN PRE-PILOT CHECKPOINT ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "check-cn-serving-state.ps1") -Compact
    if ($LASTEXITCODE -ne 0) {
        throw "CN serving preflight failed. No US pilot mutation was started."
    }

    Write-Host "`n===== STORAGE BEFORE ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "profile-storage-capacity.ps1") `
        -Compact -OutputPath $beforeProfilePath
    if ($LASTEXITCODE -ne 0) {
        throw "Pre-pilot storage profile failed. No US pilot mutation was started."
    }

    Write-Host "`n===== APPLY EXACTLY ONE FROZEN US PACKAGE ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "replay-us-deterministic.ps1") `
        -ExpectedHistoryParts $ExpectedHistoryParts `
        -Apply -MaxPackages 1 `
        -OutputPath $replayPath
    $replayExitCode = $LASTEXITCODE

    Write-Host "`n===== STORAGE AFTER ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "profile-storage-capacity.ps1") `
        -Compact -OutputPath $afterProfilePath
    $afterProfileExitCode = $LASTEXITCODE

    Write-Host "`n===== CN POST-PILOT CHECKPOINT ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "check-cn-serving-state.ps1") -Compact
    $cnPostExitCode = $LASTEXITCODE

    if ($replayExitCode -ne 0) {
        throw "One-package US replay failed. Evidence directory preserved: $evidenceDir"
    }
    if ($afterProfileExitCode -ne 0) {
        throw "Post-pilot storage profile failed. Evidence directory preserved: $evidenceDir"
    }
    if ($cnPostExitCode -ne 0) {
        throw "CN post-pilot checkpoint failed. Evidence directory preserved: $evidenceDir"
    }
    if (-not (Test-Path -LiteralPath $replaySummaryPath -PathType Leaf)) {
        throw "One-package US replay produced no compact summary. Evidence directory preserved: $evidenceDir"
    }
    $replaySummary = Get-Content -LiteralPath $replaySummaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $replaySummary.apply_one_package_ok -or [int]$replaySummary.processed_count -ne 1 -or [int]$replaySummary.source_preflight_runs -ne 1) {
        throw "US replay did not prove exactly one source package was processed."
    }
    if (-not $replaySummary.first_processed -or `
        [int]$replaySummary.first_processed.sequence -ne $ExpectedSequence -or `
        [string]$replaySummary.first_processed.file_name -ne $ExpectedFileName -or `
        ([string]$replaySummary.first_processed.sha256).ToLowerInvariant() -ne $expectedPackageSha) {
        throw "US replay processed package identity does not match the frozen pilot package."
    }
    if ([int]$replaySummary.remaining_count -ne $ExpectedRemainingAfter) {
        throw "US replay remaining-count drifted after the exactly-one-package pilot."
    }
    Write-Host "FROZEN_US_PILOT_APPLY_IDENTITY_OK"
    Write-Host "Processed package: $($replaySummary.first_processed.file_name)"
    Write-Host "Remaining after: $($replaySummary.remaining_count)"

    Write-Host "`n===== BUILD PILOT RECEIPT ====="
    $evidenceDirDocker = $evidenceDir.Replace('\\', '/')
    $repoAppDocker = (Join-Path $repoRoot "app").Replace('\\', '/')
    $jsonLines = & docker compose run --rm --no-deps -T `
        --volume "${repoAppDocker}:/app/app:ro" `
        --volume "${evidenceDirDocker}:/pilot" `
        worker python -m app.us.capacity_pilot `
        --engine-sha $engineSha `
        --dry-run /pilot/dry_run.json `
        --replay /pilot/replay_one_package.json `
        --before-profile /pilot/storage_before.json `
        --after-profile /pilot/storage_after.json `
        --output /pilot/pilot_receipt.json
    $receiptExitCode = $LASTEXITCODE
    if ($jsonLines) { $jsonLines | Write-Host }
    if ($receiptExitCode -ne 0 -or -not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        throw "US capacity pilot receipt failed closed. Evidence directory preserved: $evidenceDir"
    }
    $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($receipt.status -ne "PASS" -or -not $receipt.projection_input_ready) {
        throw "US capacity pilot did not produce an accepted projection input receipt."
    }

    Write-Host "Pilot raw bytes: $($receipt.pilot.raw_bytes)"
    Write-Host "Pilot Warm bytes: $($receipt.pilot.warm_bytes)"
    Write-Host "Pilot Hot bytes: $($receipt.pilot.hot_bytes)"
    Write-Host "Pilot rows: $($receipt.pilot.rows)"
    Write-Host "Pilot receipt: $receiptPath"
    Write-Host "US_BOUNDED_CAPACITY_PILOT_PASS"
}
finally {
    Pop-Location
}
