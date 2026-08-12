function Get-DataEngineReplayTelemetryHostSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string]$HostStoragePath = ""
    )

    if (-not $HostStoragePath) {
        $envPath = Join-Path $RepoRoot ".env"
        if (Test-Path -LiteralPath $envPath -PathType Leaf) {
            $rawLine = Get-Content -LiteralPath $envPath -Encoding UTF8 |
                Where-Object { $_ -match '^\s*RAW_DATA_PATH\s*=' } |
                Select-Object -First 1
            if ($rawLine) {
                $HostStoragePath = (($rawLine -split '=', 2)[1]).Trim().Trim('"').Trim("'")
            }
        }
    }
    if (-not $HostStoragePath) {
        $HostStoragePath = $RepoRoot
    }
    if (-not [System.IO.Path]::IsPathRooted($HostStoragePath)) {
        $HostStoragePath = Join-Path $RepoRoot $HostStoragePath
    }

    $existingPath = $HostStoragePath
    while (-not (Test-Path -LiteralPath $existingPath) -and $existingPath) {
        $parent = Split-Path -Parent $existingPath
        if (-not $parent -or $parent -eq $existingPath) { break }
        $existingPath = $parent
    }
    if (-not (Test-Path -LiteralPath $existingPath)) {
        throw "Unable to resolve replay telemetry host storage path: $HostStoragePath"
    }

    $resolvedPath = (Resolve-Path -LiteralPath $existingPath).Path
    $driveRoot = [System.IO.Path]::GetPathRoot($resolvedPath)
    if (-not $driveRoot) {
        throw "Unable to determine replay telemetry host drive: $resolvedPath"
    }
    $drive = [System.IO.DriveInfo]::new($driveRoot)
    return [ordered]@{
        requested_path = $HostStoragePath
        resolved_path = $resolvedPath
        drive_root = $driveRoot
        free_space = [int64]$drive.AvailableFreeSpace
        total_space = [int64]$drive.TotalSize
    }
}

function Get-DataEngineReplayTelemetryRuntimeSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [ValidateSet("CN", "US", "US_ASSIGNMENT", "US_TTAB")]
        [string]$Jurisdiction
    )

    $args = @(
        "compose", "run", "--rm", "--no-deps", "-T",
        "--volume", "${RepoRoot}\app:/app/app:ro",
        "worker", "python", "-m", "app.replay_telemetry",
        "--jurisdiction", $Jurisdiction,
        "--compact"
    )
    $jsonLines = & docker @args
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"
    if ($exitCode -ne 0 -or -not $json.Trim()) {
        throw "Replay telemetry runtime snapshot failed with exit code $exitCode."
    }
    try {
        return $json | ConvertFrom-Json
    }
    catch {
        throw "Replay telemetry runtime snapshot returned invalid JSON: $($_.Exception.Message)"
    }
}

function Start-DataEngineReplayTelemetry {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("CN", "US_APPLICATION", "US_ASSIGNMENT", "US_TTAB")]
        [string]$Domain,
        [Parameter(Mandatory = $true)]
        [ValidateSet("CN", "US", "US_ASSIGNMENT", "US_TTAB")]
        [string]$Jurisdiction,
        [Parameter(Mandatory = $true)][string]$CommandName
    )

    $repoRoot = Split-Path -Parent $PSScriptRoot
    $runId = "{0}_{1}" -f (Get-Date -Format "yyyyMMdd_HHmmss"), ([guid]::NewGuid().ToString("N"))
    $runDirectory = Join-Path $repoRoot "reports\replay_runs"
    New-Item -ItemType Directory -Force -Path $runDirectory | Out-Null

    $gitSha = "UNKNOWN"
    try {
        $candidate = (& git -C $repoRoot rev-parse HEAD 2>$null | Select-Object -First 1).Trim()
        if ($LASTEXITCODE -eq 0 -and $candidate) { $gitSha = $candidate }
    }
    catch {
        Write-Warning "Replay telemetry could not read Git HEAD: $($_.Exception.Message)"
    }

    $runtime = $null
    $runtimeError = ""
    try {
        $runtime = Get-DataEngineReplayTelemetryRuntimeSnapshot -RepoRoot $repoRoot -Jurisdiction $Jurisdiction
    }
    catch {
        $runtimeError = $_.Exception.Message
        Write-Warning "Replay telemetry start runtime snapshot failed: $runtimeError"
    }

    $host = $null
    $hostError = ""
    try {
        $host = Get-DataEngineReplayTelemetryHostSnapshot -RepoRoot $repoRoot
    }
    catch {
        $hostError = $_.Exception.Message
        Write-Warning "Replay telemetry start host snapshot failed: $hostError"
    }

    $context = [ordered]@{
        telemetry_version = "DATA_ENGINE_REPLAY_TELEMETRY_V1"
        run_id = $runId
        domain = $Domain
        jurisdiction = $Jurisdiction
        command = $CommandName
        git_sha = $gitSha
        started_at = [DateTimeOffset]::UtcNow.ToString("o")
        repo_root = $repoRoot
        start = [ordered]@{
            runtime = $runtime
            runtime_error = $runtimeError
            host = $host
            host_error = $hostError
        }
        start_report = Join-Path $runDirectory "${runId}.start.json"
        final_report = Join-Path $runDirectory "${runId}.json"
        ledger_path = Join-Path $repoRoot "reports\replay_ledger.jsonl"
    }

    $context | ConvertTo-Json -Depth 100 | Set-Content -Encoding UTF8 $context.start_report
    return [pscustomobject]$context
}

function Complete-DataEngineReplayTelemetry {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$ErrorMessage = "",
        [string]$ReportPath = ""
    )

    if (-not $Context) { return }

    try {
        $endRuntime = $null
        $endRuntimeError = ""
        try {
            $endRuntime = Get-DataEngineReplayTelemetryRuntimeSnapshot `
                -RepoRoot $Context.repo_root `
                -Jurisdiction $Context.jurisdiction
        }
        catch {
            $endRuntimeError = $_.Exception.Message
            Write-Warning "Replay telemetry end runtime snapshot failed: $endRuntimeError"
        }

        $endHost = $null
        $endHostError = ""
        try {
            $endHost = Get-DataEngineReplayTelemetryHostSnapshot -RepoRoot $Context.repo_root
        }
        catch {
            $endHostError = $_.Exception.Message
            Write-Warning "Replay telemetry end host snapshot failed: $endHostError"
        }

        $finishedAt = [DateTimeOffset]::UtcNow
        $startedAt = [DateTimeOffset]::Parse($Context.started_at)
        $deltas = [ordered]@{}

        if ($Context.start.runtime -and $endRuntime) {
            $deltas.clickhouse_active_bytes = [int64]$endRuntime.clickhouse.active_bytes - [int64]$Context.start.runtime.clickhouse.active_bytes
            $deltas.clickhouse_active_rows = [int64]$endRuntime.clickhouse.active_rows - [int64]$Context.start.runtime.clickhouse.active_rows
            $deltas.clickhouse_stage_bytes = [int64]$endRuntime.clickhouse.active_stage_bytes - [int64]$Context.start.runtime.clickhouse.active_stage_bytes

            $beforeCounts = $Context.start.runtime.packages.status_counts
            $afterCounts = $endRuntime.packages.status_counts
            $statusNames = @(
                @($beforeCounts.PSObject.Properties.Name) + @($afterCounts.PSObject.Properties.Name) |
                    Sort-Object -Unique
            )
            $packageDeltas = [ordered]@{}
            foreach ($name in $statusNames) {
                $beforeValue = if ($beforeCounts.PSObject.Properties.Name -contains $name) { [int64]$beforeCounts.$name } else { 0 }
                $afterValue = if ($afterCounts.PSObject.Properties.Name -contains $name) { [int64]$afterCounts.$name } else { 0 }
                $packageDeltas[$name] = $afterValue - $beforeValue
            }
            $deltas.package_status_counts = $packageDeltas
        }

        if ($Context.start.host -and $endHost) {
            $deltas.host_free_space = [int64]$endHost.free_space - [int64]$Context.start.host.free_space
        }

        $reportFile = $null
        if ($ReportPath) {
            $candidatePath = $ReportPath
            if (-not [System.IO.Path]::IsPathRooted($candidatePath)) {
                $candidatePath = Join-Path $Context.repo_root $candidatePath
            }
            if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
                $resolvedReport = (Resolve-Path -LiteralPath $candidatePath).Path
                $reportFile = [ordered]@{
                    path = $resolvedReport
                    sha256 = (Get-FileHash -LiteralPath $resolvedReport -Algorithm SHA256).Hash.ToLowerInvariant()
                }
            }
        }

        $record = [ordered]@{
            telemetry_version = "DATA_ENGINE_REPLAY_TELEMETRY_V1"
            run_id = $Context.run_id
            domain = $Context.domain
            jurisdiction = $Context.jurisdiction
            command = $Context.command
            git_sha = $Context.git_sha
            started_at = $Context.started_at
            finished_at = $finishedAt.ToString("o")
            duration_seconds = [math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
            status = $Status
            error = $ErrorMessage
            source_fact_mutation = "OBSERVED_ONLY_NOT_TELEMETRY_WRITTEN_TO_FACT_DATABASES"
            start = $Context.start
            end = [ordered]@{
                runtime = $endRuntime
                runtime_error = $endRuntimeError
                host = $endHost
                host_error = $endHostError
            }
            deltas = $deltas
            replay_report = $reportFile
        }

        $json = $record | ConvertTo-Json -Depth 100
        $json | Set-Content -Encoding UTF8 $Context.final_report
        ($record | ConvertTo-Json -Depth 100 -Compress) | Add-Content -Encoding UTF8 $Context.ledger_path
        Write-Host "Replay telemetry: $($Context.final_report)"
        Write-Host "Replay ledger: $($Context.ledger_path)"
    }
    catch {
        Write-Warning "Replay telemetry finalization failed without changing replay outcome: $($_.Exception.Message)"
    }
}
