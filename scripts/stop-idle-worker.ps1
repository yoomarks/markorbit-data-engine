param(
    [switch]$StopIdleWorker
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $postgres = docker compose ps --status running -q postgres
    if ($LASTEXITCODE -ne 0 -or -not $postgres) {
        throw "PostgreSQL must be running before the global idle-worker gate."
    }

    $statusCommand = 'psql -At -F "|" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT (SELECT count(*) FROM control.job_run WHERE finished_at IS NULL), (SELECT count(*) FROM control.source_package WHERE status = ''PROCESSING'');"'
    $statusLines = @(& docker compose exec -T postgres sh -lc $statusCommand)
    $statusExit = $LASTEXITCODE
    if ($statusExit -ne 0) {
        throw "Unable to inspect global Data Engine lifecycle state."
    }

    $matches = @($statusLines | Where-Object { $_ -match '^\d+\|\d+$' })
    if ($matches.Count -ne 1) {
        throw "Global lifecycle query returned an unexpected result."
    }

    $parts = $matches[0].Split('|')
    $activeJobs = [int64]$parts[0]
    $processingPackages = [int64]$parts[1]

    Write-Host "Global unfinished jobs: $activeJobs"
    Write-Host "Global PROCESSING packages: $processingPackages"

    if ($activeJobs -ne 0 -or $processingPackages -ne 0) {
        $detailCommand = 'psql -At -F "|" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT ''JOB'', coalesce(domain, ''''), coalesce(status, ''''), coalesce(id::text, '''') FROM control.job_run WHERE finished_at IS NULL ORDER BY started_at NULLS LAST LIMIT 20; SELECT ''PACKAGE'', coalesce(jurisdiction, ''''), coalesce(source_kind, ''''), coalesce(id::text, '''') FROM control.source_package WHERE status = ''PROCESSING'' ORDER BY updated_at NULLS LAST LIMIT 20;"'
        $details = @(& docker compose exec -T postgres sh -lc $detailCommand)
        if ($LASTEXITCODE -eq 0 -and $details) {
            Write-Host "Active lifecycle rows:"
            $details | Write-Host
        }
        throw "Tracked Data Engine work is active. The persistent worker must not be stopped."
    }

    Write-Host "GLOBAL_DATA_ENGINE_IDLE_OK"

    $worker = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect persistent worker state."
    }

    if (-not $worker) {
        Write-Host "Persistent worker is already stopped."
        Write-Host "IDLE_WORKER_STOP_GATE_PASS"
        return
    }

    if (-not $StopIdleWorker) {
        throw "Persistent worker is running but globally idle. Re-run this single operator with explicit -StopIdleWorker."
    }

    Write-Host "Stopping globally idle persistent worker only..."
    & docker compose stop worker | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to stop the globally idle persistent worker."
    }

    $workerAfter = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to verify persistent worker state after stop."
    }
    if ($workerAfter) {
        throw "Persistent worker is still running after the stop request."
    }

    Write-Host "IDLE_WORKER_STOPPED_OK"
    Write-Host "IDLE_WORKER_STOP_GATE_PASS"
}
finally {
    Pop-Location
}
