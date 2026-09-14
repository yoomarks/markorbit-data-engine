param(
    [string]$ManifestRelativePath = "us_assignment/corpus_manifest.json",
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedApplicationHistoryParts,
    [switch]$Apply,
    [switch]$All,
    [switch]$ResumeFailed,
    [ValidateRange(1, 1000000)][int]$MaxPackages = 1,
    [string]$AuthorityPlanRelativePath = "",
    [string]$AuthorityToken = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "replay-telemetry.ps1")

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Docker Compose worker state." }
if ($worker) { throw "Persistent worker is running. Stop it before deterministic Assignment replay." }

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before deterministic Assignment replay."
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$mode = if ($Apply) { "apply" } else { "dryrun" }
if (-not $OutputPath) {
    $OutputPath = Join-Path "reports" "us_assignment_replay_${mode}_$timestamp.json"
}

$authorityConsumed = $false
$authorityPlan = ""
$authorityReceipt = ""
$effectiveResumeFailed = [bool]$ResumeFailed
$controlRoot = "/data/control"

function Finalize-AssignmentAuthority {
    param(
        [bool]$Success,
        [string]$ErrorMessage = ""
    )
    if (-not $authorityConsumed) { return }
    $finalizeArgs = @(
        "run", "--rm", "--no-deps", "-T", "worker",
        "python", "-m", "app.us_assignment.production_authority",
        "finalize", "--plan", $authorityPlan,
        "--receipt", $authorityReceipt,
        "--report-path", $OutputPath
    )
    if ($Success) {
        $finalizeArgs += "--success"
    }
    elseif ($ErrorMessage) {
        $finalizeArgs += @("--error-message", $ErrorMessage)
    }
    & docker compose @finalizeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "US Assignment authority finalization failed."
    }
}

if ($Apply) {
    if (-not $All) {
        throw "Production-authorized US Assignment replay must use -All."
    }
    if (-not $AuthorityPlanRelativePath -or -not $AuthorityToken) {
        throw "-Apply requires -AuthorityPlanRelativePath and the exact -AuthorityToken."
    }

    $dirty = git status --porcelain --untracked-files=no
    if ($LASTEXITCODE -ne 0 -or $dirty) {
        throw "Production Assignment replay requires a clean tracked worktree."
    }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $head.Length -ne 40) {
        throw "Unable to resolve local HEAD before Assignment replay."
    }
    $remoteLine = git ls-remote origin refs/heads/main
    if ($LASTEXITCODE -ne 0 -or -not $remoteLine) {
        throw "Unable to verify live origin/main before Assignment replay."
    }
    $remoteMain = (($remoteLine -split "\s+")[0]).Trim().ToLowerInvariant()
    if ($remoteMain -ne $head) {
        throw "Local HEAD is not live origin/main; frozen Assignment authority is invalid."
    }

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "assert-domain-apply-gate.ps1") `
        -TargetDomain "US_ASSIGNMENT" `
        -ExpectedApplicationHistoryParts $ExpectedApplicationHistoryParts
    if ($LASTEXITCODE -ne 0) {
        throw "US Assignment apply gate failed; replay was not started."
    }

    $authorityPlan = "$controlRoot/" + ($AuthorityPlanRelativePath -replace '\\', '/')
    $receiptRelative = "us_assignment/authority_receipt_${timestamp}.json"
    $authorityReceipt = "$controlRoot/$receiptRelative"
    $consumeArgs = @(
        "run", "--build", "--rm", "--no-deps", "-T", "worker",
        "python", "-m", "app.us_assignment.production_authority",
        "consume", "--plan", $authorityPlan,
        "--control-root", $controlRoot,
        "--expected-main", $head,
        "--authority-token", $AuthorityToken,
        "--output", $authorityReceipt
    )
    $authorityLines = & docker compose @consumeArgs
    $authorityExit = $LASTEXITCODE
    if ($authorityExit -ne 0) {
        throw "US Assignment production authority was not consumed; replay was not started."
    }
    $authorityJson = $authorityLines -join "`n"
    $authorityResult = $authorityJson | ConvertFrom-Json
    if ($authorityResult.decision -ne "US_ASSIGNMENT_PRODUCTION_REPLAY_AUTHORIZED") {
        throw "Unexpected US Assignment authority decision."
    }
    $authorityConsumed = $true
    $effectiveResumeFailed = [bool]$authorityResult.resume_failed
    Write-Host $authorityJson
    Write-Host "Authority receipt: $receiptRelative"
}

$telemetry = $null
$telemetryStatus = "NOT_RECORDED"
$telemetryError = ""
if ($Apply) {
    try {
        $telemetry = Start-DataEngineReplayTelemetry `
            -Domain "US_ASSIGNMENT" `
            -Jurisdiction "US_ASSIGNMENT" `
            -CommandName "replay-us-assignment-deterministic.ps1"
        $telemetryStatus = "COMMAND_RUNNING"
    }
    catch {
        Write-Warning "Replay telemetry start failed without blocking Assignment replay: $($_.Exception.Message)"
    }
}

try {
    if ($Apply) {
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "apply-us-assignment-schema.ps1")
        if ($LASTEXITCODE -ne 0) { throw "US Assignment schema gate failed." }
    }

    $manifest = "$controlRoot/" + ($ManifestRelativePath -replace '\\', '/')
    $args = @(
        "run", "--build", "--rm", "--no-deps", "-T", "worker",
        "python", "-m", "app.us_assignment.corpus_replay",
        "--manifest", $manifest,
        "--max-packages", "$MaxPackages"
    )
    if ($Apply) {
        $args += @(
            "--apply", "--all",
            "--authority-plan", $authorityPlan,
            "--authority-receipt", $authorityReceipt,
            "--authority-control-root", $controlRoot
        )
    }
    elseif ($All) {
        $args += "--all"
    }
    if ($effectiveResumeFailed) { $args += "--resume-failed" }

    $jsonLines = & docker compose @args
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) { New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null }
    $json | Set-Content -Encoding UTF8 $OutputPath
    Write-Host $json
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0) { throw "Deterministic US Assignment replay failed. See the JSON report above." }
    $report = $json | ConvertFrom-Json
    if ($report.status -eq "RETRY_REQUIRED") {
        throw "US Assignment replay stopped at a failed package; freeze a new authority plan before retry."
    }
    if ($report.status -in @("BLOCKED", "FAILED", "BUSY")) {
        throw "Deterministic US Assignment replay stopped: $($report.status)"
    }
    if ($Apply -and $report.status -ne "COMPLETE") {
        throw "Production-authorized US Assignment replay did not complete the frozen corpus."
    }
    if ($telemetry) {
        $telemetryStatus = [string]$report.status
    }
    if ($authorityConsumed) {
        Finalize-AssignmentAuthority -Success $true
        $authorityConsumed = $false
    }
}
catch {
    $originalError = $_.Exception.Message
    if ($telemetry) {
        $telemetryStatus = "COMMAND_FAILED"
        $telemetryError = $originalError
    }
    if ($authorityConsumed) {
        try {
            Finalize-AssignmentAuthority -Success $false -ErrorMessage $originalError
            $authorityConsumed = $false
        }
        catch {
            Write-Warning "Assignment authority failure finalization also failed: $($_.Exception.Message)"
        }
    }
    throw
}
finally {
    if ($telemetry) {
        Complete-DataEngineReplayTelemetry `
            -Context $telemetry `
            -Status $telemetryStatus `
            -ErrorMessage $telemetryError `
            -ReportPath $OutputPath
    }
}
