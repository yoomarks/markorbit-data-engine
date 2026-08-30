param(
    [Parameter(Mandatory = $true)]
    [string]$RunId,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedMainSha,

    [Parameter(Mandatory = $true)]
    [Int64]$ExpectedStartedAtEpochMicros,

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
    if ($ExpectedStartedAtEpochMicros -le 0) {
        throw "ExpectedStartedAtEpochMicros must be positive."
    }
    if ($ExpectedJobType -notmatch '^[A-Z0-9_]+$') {
        throw "ExpectedJobType must contain only uppercase letters, digits, and underscore."
    }

    Write-Host "===== EXACT-MAIN ORPHAN RECONCILIATION GATE ====="
    if (git status --porcelain) {
        throw "Working tree must be clean before orphan reconciliation."
    }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expectedSha"
    if ($head -ne $expectedSha -or $originMain -ne $expectedSha) {
        throw "Exact-main gate failed."
    }
    Write-Host "EXACT_MAIN_ORPHAN_RECONCILIATION_OK"

    $postgres = docker compose ps --status running -q postgres
    if ($LASTEXITCODE -ne 0 -or -not $postgres) {
        throw "PostgreSQL must be running."
    }
    $clickhouse = docker compose ps --status running -q clickhouse
    if ($LASTEXITCODE -ne 0 -or -not $clickhouse) {
        throw "ClickHouse must be running."
    }

    function Get-WorkerIds {
        param([switch]$AllStates)
        if ($AllStates) {
            $raw = @(& docker compose ps -a -q worker)
        }
        else {
            $raw = @(& docker compose ps --status running -q worker)
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to enumerate worker containers."
        }
        return @(
            $raw |
                ForEach-Object { $_.Trim() } |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
    }

    $runningWorkers = @(Get-WorkerIds)
    $allWorkers = @(Get-WorkerIds -AllStates)
    Write-Host "worker_running_count=$($runningWorkers.Count)"
    Write-Host "worker_container_count_all_states=$($allWorkers.Count)"
    if ($runningWorkers.Count -ne 0 -or $allWorkers.Count -ne 0) {
        throw "Orphan reconciliation requires zero worker containers in all states."
    }
    Write-Host "ZERO_WORKER_CONTAINER_GATE_OK"

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
            "psql", "-v", "ON_ERROR_STOP=1", "-At", "-F", "|",
            "-U", $script:postgresUser,
            "-d", $script:postgresDb,
            "-c", $Sql
        )
        $lines = @(& docker @args)
        if ($LASTEXITCODE -ne 0) {
            throw "PostgreSQL reconciliation query failed."
        }
        return $lines
    }

    $script:postgresUser = Get-ContainerEnvValue -Service "postgres" -Name "POSTGRES_USER"
    $script:postgresDb = Get-ContainerEnvValue -Service "postgres" -Name "POSTGRES_DB"

    Write-Host "`n===== PRE-MUTATION DATABASE GATES ====="
    $globalUnfinishedSql = "SELECT count(*) FROM control.job_run WHERE finished_at IS NULL;"
    $globalProcessingSql = "SELECT count(*) FROM control.source_package WHERE status = 'PROCESSING';"
    $targetSql = @"
SELECT count(*)
FROM control.job_run
WHERE run_id = '$canonicalRunId'::uuid
  AND job_type = '$ExpectedJobType'
  AND trigger_type = 'ADMIN_UI'
  AND status = 'RUNNING'
  AND finished_at IS NULL
  AND payload->>'task_kind' = 'DOMAIN_CONTROL'
  AND payload->>'domain' = 'CN'
  AND payload->>'action' = 'CONTINUE'
  AND coalesce(payload->>'stop_requested', 'false') = 'false'
  AND round(extract(epoch FROM started_at) * 1000000)::bigint = $ExpectedStartedAtEpochMicros;
"@
    $pgActiveSql = @'
SELECT count(*)
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
  AND state <> 'idle';
'@

    $unfinished = ((Invoke-PostgresSql -Sql $globalUnfinishedSql) -join '').Trim()
    $processing = ((Invoke-PostgresSql -Sql $globalProcessingSql) -join '').Trim()
    $targetMatches = ((Invoke-PostgresSql -Sql $targetSql) -join '').Trim()
    $pgActive = ((Invoke-PostgresSql -Sql $pgActiveSql) -join '').Trim()

    foreach ($value in @($unfinished, $processing, $targetMatches, $pgActive)) {
        if ($value -notmatch '^\d+$') {
            throw "Unexpected numeric gate result: $value"
        }
    }

    Write-Host "global_unfinished_jobs=$unfinished"
    Write-Host "global_processing_packages=$processing"
    Write-Host "exact_target_matches=$targetMatches"
    Write-Host "postgres_non_idle_sessions=$pgActive"

    if ([int64]$unfinished -ne 1) {
        throw "Expected exactly one unfinished job globally before reconciliation."
    }
    if ([int64]$processing -ne 0) {
        throw "PROCESSING source packages exist; reconciliation refused."
    }
    if ([int64]$targetMatches -ne 1) {
        throw "Exact orphan target CAS preconditions no longer match."
    }
    if ([int64]$pgActive -ne 0) {
        throw "PostgreSQL has non-idle sessions; reconciliation refused."
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
        if ($LASTEXITCODE -ne 0) {
            throw "ClickHouse active-query gate failed."
        }
        return $lines
    }

    $activeChSql = @'
SELECT count()
FROM system.processes
WHERE query NOT LIKE '%FROM system.processes%'
FORMAT TabSeparatedRaw
'@
    $activeCh = ((Invoke-ClickHouseSql -Sql $activeChSql) -join '').Trim()
    if ($activeCh -notmatch '^\d+$') {
        throw "Unexpected ClickHouse active-query count: $activeCh"
    }
    Write-Host "clickhouse_active_queries=$activeCh"
    if ([int64]$activeCh -ne 0) {
        throw "ClickHouse has active queries; reconciliation refused."
    }
    Write-Host "NO_LIVE_WORKLOAD_GATE_OK"

    Write-Host "`n===== FINAL ZERO-WORKER RECHECK ====="
    $runningWorkers = @(Get-WorkerIds)
    $allWorkers = @(Get-WorkerIds -AllStates)
    Write-Host "worker_running_count_final=$($runningWorkers.Count)"
    Write-Host "worker_container_count_all_states_final=$($allWorkers.Count)"
    if ($runningWorkers.Count -ne 0 -or $allWorkers.Count -ne 0) {
        throw "Worker state changed before reconciliation; refused."
    }

    Write-Host "`n===== EXACT COMPARE-AND-SET RECONCILIATION ====="
    $reconcileSql = @'
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('markorbit:admin-domain-task-queue'));
DO $reconcile$
DECLARE
    affected integer;
    processing_count integer;
BEGIN
    SELECT count(*) INTO processing_count
    FROM control.source_package
    WHERE status = 'PROCESSING';
    IF processing_count <> 0 THEN
        RAISE EXCEPTION 'PROCESSING source packages appeared during reconciliation';
    END IF;

    UPDATE control.job_run
    SET status = 'INTERRUPTED',
        finished_at = now(),
        metrics = coalesce(metrics, '{}'::jsonb) || jsonb_build_object(
            'lifecycle_reconciliation',
            jsonb_build_object(
                'reason', 'ORPHANED_ADMIN_NO_WORKER',
                'operator', 'scripts/reconcile-orphaned-admin-job-no-worker.ps1',
                'reconciled_at', now(),
                'expected_started_at_epoch_micros', __EXPECTED_EPOCH__,
                'worker_containers', 0,
                'processing_packages', 0
            )
        ),
        error_message = 'Reconciled orphaned Admin lifecycle row after target-host proof: no worker containers, no PROCESSING packages, and no active PostgreSQL/ClickHouse workload.'
    WHERE run_id = '__RUN_ID__'::uuid
      AND job_type = '__JOB_TYPE__'
      AND trigger_type = 'ADMIN_UI'
      AND status = 'RUNNING'
      AND finished_at IS NULL
      AND payload->>'task_kind' = 'DOMAIN_CONTROL'
      AND payload->>'domain' = 'CN'
      AND payload->>'action' = 'CONTINUE'
      AND coalesce(payload->>'stop_requested', 'false') = 'false'
      AND round(extract(epoch FROM started_at) * 1000000)::bigint = __EXPECTED_EPOCH__;

    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN
        RAISE EXCEPTION 'Orphan Admin CAS mismatch; expected exactly one row, updated %', affected;
    END IF;
END
$reconcile$;
SELECT 'RECONCILED',
       run_id::text,
       job_type,
       trigger_type,
       status,
       round(extract(epoch FROM started_at) * 1000000)::bigint,
       (finished_at IS NOT NULL)::text,
       coalesce(metrics->'lifecycle_reconciliation'->>'reason', '')
FROM control.job_run
WHERE run_id = '__RUN_ID__'::uuid;
COMMIT;
'@
    $reconcileSql = $reconcileSql.Replace('__RUN_ID__', $canonicalRunId)
    $reconcileSql = $reconcileSql.Replace('__JOB_TYPE__', $ExpectedJobType)
    $reconcileSql = $reconcileSql.Replace('__EXPECTED_EPOCH__', $ExpectedStartedAtEpochMicros.ToString([System.Globalization.CultureInfo]::InvariantCulture))

    $result = @(Invoke-PostgresSql -Sql $reconcileSql)
    $reconciledLine = @($result | Where-Object { $_ -like 'RECONCILED|*' })
    if ($reconciledLine.Count -ne 1) {
        throw "Reconciliation committed but verification marker was not returned exactly once."
    }
    Write-Host $reconciledLine[0]

    Write-Host "`n===== POST-RECONCILIATION VERIFICATION ====="
    $verifySql = @"
SELECT status,
       (finished_at IS NOT NULL)::text,
       coalesce(metrics->'lifecycle_reconciliation'->>'reason', ''),
       count(*) OVER ()
FROM control.job_run
WHERE run_id = '$canonicalRunId'::uuid;
"@
    $verify = @((Invoke-PostgresSql -Sql $verifySql))
    if ($verify.Count -ne 1) {
        throw "Post-reconciliation target verification failed."
    }
    Write-Host "target_verification=$($verify[0])"

    $unfinishedAfter = ((Invoke-PostgresSql -Sql $globalUnfinishedSql) -join '').Trim()
    Write-Host "global_unfinished_jobs_after=$unfinishedAfter"
    if ($unfinishedAfter -ne '0') {
        throw "Unexpected unfinished jobs remain after reconciliation."
    }

    Write-Host "reconciliation_performed=True"
    Write-Host "reconciliation_status=INTERRUPTED"
    Write-Host "worker_start_performed=False"
    Write-Host "worker_stop_performed=False"
    Write-Host "schema_apply_performed=False"
    Write-Host "corpus_replay_performed=False"
    Write-Host "permission_repair_performed=False"
    Write-Host "ORPHANED_ADMIN_JOB_CAS_RECONCILIATION_COMPLETE"
}
finally {
    Pop-Location
}
