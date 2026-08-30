param(
    [Parameter(Mandatory = $true)]
    [string]$RunId,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedMainSha,

    [string]$ExpectedJobType = "CN_ADMIN_CONTINUE",
    [int]$MaxRelevantLogLines = 120
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
    if ($MaxRelevantLogLines -lt 1 -or $MaxRelevantLogLines -gt 1000) {
        throw "MaxRelevantLogLines must be between 1 and 1000."
    }

    Write-Host "===== EXACT-MAIN READ-ONLY GATE ====="
    if (git status --porcelain) {
        throw "Working tree must be clean before the runtime diagnostic."
    }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expectedSha"
    if ($head -ne $expectedSha -or $originMain -ne $expectedSha) {
        throw "Exact-main gate failed."
    }
    Write-Host "EXACT_MAIN_READ_ONLY_OK"

    $postgres = docker compose ps --status running -q postgres
    if ($LASTEXITCODE -ne 0 -or -not $postgres) {
        throw "PostgreSQL must be running."
    }
    $clickhouse = docker compose ps --status running -q clickhouse
    if ($LASTEXITCODE -ne 0 -or -not $clickhouse) {
        throw "ClickHouse must be running."
    }
    $workerLines = @(& docker compose ps --status running -q worker)
    if ($LASTEXITCODE -ne 0 -or $workerLines.Count -ne 1) {
        throw "Exactly one persistent worker must be running for ownership diagnosis."
    }
    $workerId = $workerLines[0].Trim()
    Write-Host "POSTGRES_CLICKHOUSE_WORKER_RUNNING_OK"

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
        throw "Job is no longer RUNNING; do not diagnose it as a stale RUNNING owner."
    }

    $payloadSql = "SELECT payload::text FROM control.job_run WHERE run_id = '$canonicalRunId'::uuid;"
    $metricsSql = "SELECT metrics::text FROM control.job_run WHERE run_id = '$canonicalRunId'::uuid;"
    $errorSql = "SELECT coalesce(error_message, '') FROM control.job_run WHERE run_id = '$canonicalRunId'::uuid;"
    Write-Host "payload=$((Invoke-PostgresSql -Sql $payloadSql) -join '')"
    Write-Host "metrics=$((Invoke-PostgresSql -Sql $metricsSql) -join '')"
    Write-Host "error_message=$((Invoke-PostgresSql -Sql $errorSql) -join '')"

    Write-Host "`n===== WORKER CONTAINER OWNERSHIP ====="
    $inspectLines = @(& docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}|{{.State.Pid}}|{{.State.Status}}|{{.Image}}' $workerId)
    if ($LASTEXITCODE -ne 0 -or $inspectLines.Count -ne 1) {
        throw "Unable to inspect the persistent worker container."
    }
    $inspect = $inspectLines[0].Split('|')
    if ($inspect.Count -ne 5) {
        throw "Persistent worker inspect result had an unexpected shape."
    }
    $workerStartedAt = $inspect[0]
    $workerRestartCount = $inspect[1]
    $workerPid = $inspect[2]
    $workerState = $inspect[3]
    $workerImageId = $inspect[4]
    Write-Host "worker_container_id=$workerId"
    Write-Host "worker_started_at=$workerStartedAt"
    Write-Host "worker_restart_count=$workerRestartCount"
    Write-Host "worker_pid=$workerPid"
    Write-Host "worker_state=$workerState"
    Write-Host "worker_image_id=$workerImageId"

    if ($startedAtUtc.Length -lt 19 -or $workerStartedAt.Length -lt 19) {
        throw "Unable to compare job/worker timestamps at portable second precision."
    }
    $jobStarted = [datetimeoffset]::Parse($startedAtUtc.Substring(0, 19) + "Z")
    $workerStarted = [datetimeoffset]::Parse($workerStartedAt.Substring(0, 19) + "Z")
    $workerStartedAfterJob = $workerStarted -gt $jobStarted
    Write-Host "worker_started_after_job_claim=$workerStartedAfterJob"
    Write-Host "worker_job_time_comparison_precision=SECONDS"

    Write-Host "`n===== RUNNING WORKER CODE VS EXACT CHECKOUT ====="
    $keyFiles = @(
        "app/worker.py",
        "app/admin_domain_tasks.py",
        "app/cn/full_replay.py",
        "app/cn/final_checkpoint.py",
        "app/cn/audit_acceptance_m16.py"
    )
    $hashCode = 'import hashlib,sys; p=sys.argv[1]; print(hashlib.sha256(open(p,"rb").read()).hexdigest())'
    $runtimeExact = $true
    foreach ($relative in $keyFiles) {
        $localPath = Join-Path $repoRoot ($relative -replace '/', '\')
        if (-not (Test-Path -LiteralPath $localPath)) {
            throw "Missing exact-checkout file: $relative"
        }
        $checkoutHash = (Get-FileHash -LiteralPath $localPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $containerPath = "/app/$relative"
        $runtimeLines = @(& docker compose exec -T worker python -c $hashCode $containerPath)
        if ($LASTEXITCODE -ne 0 -or $runtimeLines.Count -ne 1) {
            throw "Unable to hash runtime file inside persistent worker: $containerPath"
        }
        $runtimeHash = $runtimeLines[0].Trim().ToLowerInvariant()
        $matchesCheckout = $runtimeHash -eq $checkoutHash
        if (-not $matchesCheckout) {
            $runtimeExact = $false
        }
        Write-Host "$relative|checkout=$checkoutHash|runtime=$runtimeHash|match=$matchesCheckout"
    }
    Write-Host "worker_runtime_matches_exact_checkout=$runtimeExact"

    Write-Host "`n===== WORKER LOG OWNERSHIP EVIDENCE ====="
    $logs = @(& docker compose logs --no-color --since $startedAtUtc --tail 20000 worker 2>&1)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "worker_logs_status=UNAVAILABLE"
    }
    else {
        $escapedRun = [regex]::Escape($canonicalRunId)
        $relevant = @(
            $logs | Where-Object {
                $_ -match $escapedRun -or
                $_ -match 'Worker started' -or
                $_ -match 'Requeued .* interrupted Admin domain task' -or
                $_ -match 'Admin domain task (started|finished)' -or
                $_ -match 'Worker cycle failed'
            }
        )
        Write-Host "worker_relevant_log_lines=$($relevant.Count)"
        if ($relevant.Count -gt 0) {
            $relevant | Select-Object -Last $MaxRelevantLogLines | ForEach-Object { Write-Host $_ }
        }
    }

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

    Write-Host "`n===== CLICKHOUSE ACTIVE QUERY EVIDENCE ====="
    $clickhouseUser = Get-ContainerEnvValue -Service "clickhouse" -Name "CLICKHOUSE_USER"
    $clickhousePassword = Get-ContainerEnvValue -Service "clickhouse" -Name "CLICKHOUSE_PASSWORD" -AllowEmpty

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
        $exitCode = $LASTEXITCODE
        return [pscustomobject]@{
            ExitCode = $exitCode
            Lines = $lines
        }
    }

    $script:clickhouseUser = $clickhouseUser
    $script:clickhousePassword = $clickhousePassword
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

    Write-Host "`n===== READ-ONLY DIAGNOSTIC STOP POINT ====="
    Write-Host "reconciliation_performed=False"
    Write-Host "worker_stop_performed=False"
    Write-Host "schema_apply_performed=False"
    Write-Host "corpus_replay_performed=False"
    Write-Host "permission_repair_performed=False"
    Write-Host "ADMIN_JOB_RUNTIME_DIAGNOSTIC_COMPLETE"
}
finally {
    Pop-Location
}
