param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [ValidatePattern('^markorbit_native_rename_probe_[0-9]{8}_[0-9]{9}$')]
    [string]$ExpectedProbeDatabase = "markorbit_native_rename_probe_20260831_035202137",
    [string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545",
    [ValidateRange(5, 120)]
    [int]$ReceiveTimeoutSeconds = 15,
    [ValidateRange(5, 120)]
    [int]$QueryKillTimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $expectedSha = $ExpectedMainSha.Trim().ToLowerInvariant()
    $probeDatabase = $ExpectedProbeDatabase.Trim()
    $probeNeedle = "INSERT INTO $probeDatabase.merge_tree_probe"

    function Invoke-ClickHouseScalar([string]$Query) {
        $lines = @(& docker compose exec -T clickhouse clickhouse-client --query $Query)
        if ($LASTEXITCODE -ne 0) {
            throw "ClickHouse scalar query failed."
        }
        $values = @($lines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($values.Count -ne 1) {
            throw "ClickHouse scalar query returned an unexpected shape."
        }
        return $values[0].Trim()
    }

    function Invoke-BoundedCommand([string]$QueryId, [string]$Query) {
        if ($QueryId -notmatch '^[A-Za-z0-9_-]+$') {
            throw "Unsafe ClickHouse query_id."
        }
        $saved = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $output = @(& docker compose exec -T clickhouse clickhouse-client `
                "--query_id=$QueryId" `
                "--connect_timeout=5" `
                "--send_timeout=$ReceiveTimeoutSeconds" `
                "--receive_timeout=$ReceiveTimeoutSeconds" `
                --query $Query 2>&1)
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $saved
        }
        return [pscustomobject]@{ exit_code = $exitCode; output = @($output | ForEach-Object { [string]$_ }) }
    }

    Write-Host "===== EXACT-MAIN STUCK NATIVE PROBE RECONCILIATION ====="
    if (git status --porcelain) {
        throw "Working tree must be clean before stuck probe reconciliation."
    }
    $branch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
        throw "Stuck probe reconciliation must run from local main."
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to fetch origin/main."
    }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expectedSha"
    if ($head -ne $expectedSha -or $originMain -ne $expectedSha) {
        throw "Exact main drift detected."
    }
    Write-Host "EXACT_MAIN_STUCK_NATIVE_PROBE_OK"

    foreach ($service in @("postgres", "clickhouse")) {
        $running = @(& docker compose ps --status running -q $service | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($LASTEXITCODE -ne 0 -or $running.Count -ne 1) {
            throw "$service must have exactly one running Compose container."
        }
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop-idle-worker.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Global Data Engine idle gate failed."
    }
    $workerAll = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    Write-Host "worker_container_count_all_states=$($workerAll.Count)"
    if ($LASTEXITCODE -ne 0 -or $workerAll.Count -ne 0) {
        throw "No worker container in any state is allowed."
    }

    $clickhouseId = @(& docker compose ps --status running -q clickhouse | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })[0].Trim()
    $health = @(& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $clickhouseId 2>$null | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($LASTEXITCODE -ne 0 -or $health.Count -ne 1 -or $health[0].Trim().ToLowerInvariant() -ne "healthy") {
        throw "ClickHouse must be Docker-health healthy before stuck probe reconciliation."
    }
    Write-Host "clickhouse_docker_health=healthy"

    $schemaSnapshotBefore = Invoke-ClickHouseScalar "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL"
    Write-Host "schema_version_snapshot_before=$schemaSnapshotBefore"
    if ($schemaSnapshotBefore -ne $ExpectedSchemaSnapshot) {
        throw "Business schema snapshot drifted; refusing incident reconciliation."
    }

    $queryRows = @(& docker compose exec -T clickhouse clickhouse-client --query "SELECT query_id FROM system.processes WHERE position(query, '$probeNeedle') > 0 FORMAT TSVRaw")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect exact stuck probe query."
    }
    $queryIds = @($queryRows | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    Write-Host "exact_probe_active_query_count=$($queryIds.Count)"
    if ($queryIds.Count -gt 1) {
        throw "More than one exact stuck probe query matched; refusing reconciliation."
    }

    if ($queryIds.Count -eq 1) {
        $queryId = $queryIds[0].Trim()
        if ($queryId -notmatch '^[A-Za-z0-9_-]+$') {
            throw "Exact stuck probe query_id is unsafe."
        }
        Write-Host "exact_probe_query_id=$queryId"
        $kill = Invoke-BoundedCommand -QueryId "markorbit_stuck_probe_kill" -Query "KILL QUERY WHERE query_id = '$queryId' ASYNC"
        Write-Host "exact_probe_kill_exit_code=$($kill.exit_code)"
        foreach ($line in $kill.output) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                Write-Host "exact_probe_kill_output=$line"
            }
        }
        $remaining = 1
        for ($attempt = 0; $attempt -lt $QueryKillTimeoutSeconds; $attempt++) {
            $remaining = [int64](Invoke-ClickHouseScalar "SELECT count() FROM system.processes WHERE query_id = '$queryId'")
            if ($remaining -eq 0) {
                break
            }
            Start-Sleep -Seconds 1
        }
        Write-Host "exact_probe_query_remaining_after_kill=$remaining"
        if ($remaining -ne 0) {
            throw "Exact stuck probe query remained active after bounded kill wait. No database cleanup was attempted."
        }
    }

    $databaseExistsBefore = [int64](Invoke-ClickHouseScalar "SELECT count() FROM system.databases WHERE name = '$probeDatabase'")
    Write-Host "probe_database_exists_before_cleanup=$databaseExistsBefore"
    if ($databaseExistsBefore -gt 1) {
        throw "Unexpected database lookup shape."
    }

    if ($databaseExistsBefore -eq 1) {
        $drop = Invoke-BoundedCommand -QueryId "markorbit_stuck_probe_cleanup" -Query "DROP DATABASE IF EXISTS $probeDatabase SYNC"
        Write-Host "probe_cleanup_exit_code=$($drop.exit_code)"
        foreach ($line in $drop.output) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                Write-Host "probe_cleanup_output=$line"
            }
        }
    }

    $databaseExistsAfter = [int64](Invoke-ClickHouseScalar "SELECT count() FROM system.databases WHERE name = '$probeDatabase'")
    Write-Host "probe_database_exists_after_cleanup=$databaseExistsAfter"
    if ($databaseExistsAfter -ne 0) {
        throw "Disposable probe database still exists after bounded normal SQL cleanup. No filesystem cleanup was attempted."
    }

    $schemaSnapshotAfter = Invoke-ClickHouseScalar "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL"
    Write-Host "schema_version_snapshot_after=$schemaSnapshotAfter"
    if ($schemaSnapshotAfter -ne $ExpectedSchemaSnapshot) {
        throw "Business schema snapshot changed during stuck probe reconciliation."
    }

    Write-Host "reconciliation_performed=True"
    Write-Host "query_kill_scope=EXACT_DISPOSABLE_PROBE_ONLY"
    Write-Host "normal_sql_cleanup_only=True"
    Write-Host "manual_filesystem_cleanup_performed=False"
    Write-Host "permission_repair_performed=False"
    Write-Host "schema_apply_performed=False"
    Write-Host "corpus_replay_performed=False"
    Write-Host "worker_start_performed=False"
    Write-Host "worker_stop_performed=False"
    Write-Host "STUCK_NATIVE_MERGETREE_PROBE_RECONCILIATION_PASS"
}
finally {
    Pop-Location
}
