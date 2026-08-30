param(
    [Parameter(Mandatory = $true)]
    [string]$RunId,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedMainSha,

    [string]$ExpectedJobType = "CN_ADMIN_CONTINUE"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $canonicalRunId = ([guid]$RunId).ToString()
    $expectedSha = $ExpectedMainSha.Trim().ToLowerInvariant()
    if ($expectedSha -notmatch '^[0-9a-f]{40}$') {
        throw "ExpectedMainSha must be a full 40-character Git commit SHA."
    }

    Write-Host "===== EXACT-MAIN NO-WORKER READ-ONLY GATE ====="
    if (git status --porcelain) {
        throw "Working tree must be clean before the diagnostic."
    }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expectedSha"
    if ($head -ne $expectedSha -or $originMain -ne $expectedSha) {
        throw "Exact-main gate failed."
    }
    Write-Host "EXACT_MAIN_NO_WORKER_READ_ONLY_OK"

    $postgres = docker compose ps --status running -q postgres
    if ($LASTEXITCODE -ne 0 -or -not $postgres) {
        throw "PostgreSQL must be running."
    }
    $clickhouse = docker compose ps --status running -q clickhouse
    if ($LASTEXITCODE -ne 0 -or -not $clickhouse) {
        throw "ClickHouse must be running."
    }

    $runningWorkers = @(
        & docker compose ps --status running -q worker |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to enumerate running worker containers."
    }
    $allWorkers = @(
        & docker compose ps -a -q worker |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to enumerate worker containers across all states."
    }

    Write-Host "worker_running_count=$($runningWorkers.Count)"
    Write-Host "worker_container_count_all_states=$($allWorkers.Count)"
    if ($runningWorkers.Count -ne 0 -or $allWorkers.Count -ne 0) {
        throw "This operator is only valid when there are zero worker containers in all states."
    }
    Write-Host "worker_ownership_state=NO_WORKER_CONTAINERS"
    Write-Host "POSTGRES_CLICKHOUSE_RUNNING_NO_WORKER_OK"

    function Get-ContainerEnvValue {
        param(
            [Parameter(Mandatory = $true)]
            [string]$Service,
            [Parameter(Mandatory = $true)]
            [string]$Name,
            [switch]$AllowEmpty
        )
        $lines = @(& docker compose exec -T $Service printenv $Name)
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            if ($AllowEmpty) {
                return ""
            }
            throw "Unable to read $Name from $Service container."
        }
        $value = ($lines -join "`n").Trim()
        if (-not $AllowEmpty -and [string]::IsNullOrWhiteSpace($value)) {
            throw "$Name is empty in $Service container."
        }
        return $value
    }

    function Invoke-PostgresSql {
        param(
            [Parameter(Mandatory = $true)]
            [string]$Sql
        )
        $args = @(
            "compose", "exec", "-T", "postgres",
            "psql", "-At", "-F", "|",
            "-U", $script:postgresUser,
            "-d", $script:postgresDb,
            "-c", $Sql
        )
        $lines = @(& docker @args)
        if ($LASTEXITCODE -ne 0) {
            throw "PostgreSQL diagnostic query failed."
        }
        return $lines
    }

    $script:postgresUser = Get-ContainerEnvValue -Service "postgres" -Name "POSTGRES_USER"
    $script:postgresDb = Get-ContainerEnvValue -Service "postgres" -Name "POSTGRES_DB"

    Write-Host "`n===== EXACT JOB ROW ====="
    $summarySql = @"
SELECT job_type,
       trigger_type,
       status,
       extract(epoch FROM now() - started_at)::bigint AS age_seconds,
       to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS started_at_utc,
       coalesce(payload->>'stop_requested', 'false') AS stop_requested
FROM control.job_run
WHERE run_id = '$canonicalRunId'::uuid;
"@
    $summary = @(Invoke-PostgresSql -Sql $summarySql)
    if ($summary.Count -ne 1 -or $summary[0] -notmatch '^([^|]*)\|([^|]*)\|([^|]*)\|(\d+)\|([^|]+)\|([^|]*)$') {
        throw "Exact Admin job row was not found or returned an unexpected shape."
    }
    $parts = $summary[0].Split('|')
    $jobType = $parts[0]
    $triggerType = $parts[1]
    $jobStatus = $parts[2]
    $ageSeconds = [int64]$parts[3]
    $startedAtUtc = $parts[4]
    $stopRequested = $parts[5]

    Write-Host "run_id=$canonicalRunId"
    Write-Host "job_type=$jobType"
    Write-Host "trigger_type=$triggerType"
    Write-Host "status=$jobStatus"
    Write-Host "age_seconds=$ageSeconds"
    Write-Host "started_at_utc=$startedAtUtc"
    Write-Host "stop_requested=$stopRequested"

    if ($jobType -ne $ExpectedJobType) {
        throw "Job type mismatch: expected $ExpectedJobType, got $jobType."
    }
    if ($triggerType -ne "ADMIN_UI") {
        throw "Trigger type mismatch: expected ADMIN_UI, got $triggerType."
    }
    if ($jobStatus -ne "RUNNING") {
        throw "Job is no longer RUNNING; no orphan reconciliation review is needed."
    }

    $payloadSql = "SELECT payload::text FROM control.job_run WHERE run_id = '$canonicalRunId'::uuid;"
    $metricsSql = "SELECT metrics::text FROM control.job_run WHERE run_id = '$canonicalRunId'::uuid;"
    $errorSql = "SELECT coalesce(error_message, '') FROM control.job_run WHERE run_id = '$canonicalRunId'::uuid;"
    $processingSql = "SELECT count(*) FROM control.source_package WHERE status = 'PROCESSING';"
    $payload = ((Invoke-PostgresSql -Sql $payloadSql) -join '')
    $metrics = ((Invoke-PostgresSql -Sql $metricsSql) -join '')
    $errorMessage = ((Invoke-PostgresSql -Sql $errorSql) -join '')
    $processingRaw = ((Invoke-PostgresSql -Sql $processingSql) -join '').Trim()
    if ($processingRaw -notmatch '^\d+$') {
        throw "Unexpected PROCESSING package count: $processingRaw"
    }
    $processingCount = [int64]$processingRaw

    Write-Host "payload=$payload"
    Write-Host "metrics=$metrics"
    Write-Host "error_message=$errorMessage"
    Write-Host "global_processing_packages=$processingCount"
    Write-Host "orphan_candidate_preconditions_met=$($processingCount -eq 0)"

    Write-Host "`n===== POSTGRES ACTIVE SESSION EVIDENCE ====="
    $pgActivitySql = @'
SELECT pid,
       coalesce(application_name, ''),
       state,
       coalesce(wait_event_type, ''),
       coalesce(wait_event, ''),
       greatest(0, extract(epoch FROM now() - query_start)::bigint) AS query_age_seconds,
       left(regexp_replace(query, E'[\n\r\t]+', ' ', 'g'), 300)
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
  AND state <> 'idle'
ORDER BY query_start;
'@
    $pgActivity = @(Invoke-PostgresSql -Sql $pgActivitySql)
    Write-Host "postgres_non_idle_sessions=$($pgActivity.Count)"
    if ($pgActivity.Count -gt 0) {
        $pgActivity | ForEach-Object { Write-Host $_ }
    }

    $script:clickhouseUser = Get-ContainerEnvValue -Service "clickhouse" -Name "CLICKHOUSE_USER"
    $script:clickhousePassword = Get-ContainerEnvValue -Service "clickhouse" -Name "CLICKHOUSE_PASSWORD" -AllowEmpty

    function Invoke-ClickHouseSql {
        param(
            [Parameter(Mandatory = $true)]
            [string]$Sql
        )
        $args = @(
            "compose", "exec", "-T", "clickhouse",
            "clickhouse-client", "--user", $script:clickhouseUser
        )
        if (-not [string]::IsNullOrEmpty($script:clickhousePassword)) {
            $args += @("--password", $script:clickhousePassword)
        }
        $args += @("--query", $Sql)
        $lines = @(& docker @args)
        return [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Lines = $lines
        }
    }

    Write-Host "`n===== CLICKHOUSE ACTIVE QUERY EVIDENCE ====="
    $activeChSql = @'
SELECT query_id,
       round(elapsed, 1),
       read_rows,
       read_bytes,
       memory_usage,
       left(replaceRegexpAll(query, '[\r\n\t]+', ' '), 300)
FROM system.processes
WHERE query NOT LIKE '%FROM system.processes%'
FORMAT TabSeparatedRaw
'@
    $activeCh = Invoke-ClickHouseSql -Sql $activeChSql
    if ($activeCh.ExitCode -ne 0) {
        Write-Host "clickhouse_active_query_status=UNAVAILABLE"
    }
    else {
        Write-Host "clickhouse_active_queries=$($activeCh.Lines.Count)"
        if ($activeCh.Lines.Count -gt 0) {
            $activeCh.Lines | ForEach-Object { Write-Host $_ }
        }
    }

    Write-Host "`n===== CLICKHOUSE RECENT LONG QUERY EVIDENCE ====="
    $recentChSql = @'
SELECT event_time,
       query_duration_ms,
       read_rows,
       read_bytes,
       memory_usage,
       exception_code,
       left(replaceRegexpAll(query, '[\r\n\t]+', ' '), 300)
FROM system.query_log
WHERE type = 'QueryFinish'
  AND event_time >= now() - INTERVAL 5 DAY
  AND query_duration_ms >= 60000
  AND query NOT LIKE '%FROM system.query_log%'
ORDER BY event_time DESC
LIMIT 30
FORMAT TabSeparatedRaw
'@
    $recentCh = Invoke-ClickHouseSql -Sql $recentChSql
    if ($recentCh.ExitCode -ne 0) {
        Write-Host "clickhouse_recent_long_query_status=UNAVAILABLE"
    }
    else {
        Write-Host "clickhouse_recent_long_queries=$($recentCh.Lines.Count)"
        if ($recentCh.Lines.Count -gt 0) {
            $recentCh.Lines | ForEach-Object { Write-Host $_ }
        }
    }

    Write-Host "`n===== READ-ONLY ORPHAN REVIEW STOP POINT ====="
    Write-Host "worker_ownership_state=NO_WORKER_CONTAINERS"
    Write-Host "ownership_requires_review=True"
    Write-Host "reconciliation_performed=False"
    Write-Host "worker_start_performed=False"
    Write-Host "worker_stop_performed=False"
    Write-Host "schema_apply_performed=False"
    Write-Host "corpus_replay_performed=False"
    Write-Host "permission_repair_performed=False"
    Write-Host "ORPHANED_ADMIN_JOB_NO_WORKER_DIAGNOSTIC_COMPLETE"
}
finally {
    Pop-Location
}
