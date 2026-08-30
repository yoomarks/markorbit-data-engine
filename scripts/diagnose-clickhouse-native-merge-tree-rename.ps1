param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$EvidenceRoot = "reports"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $expectedSha = $ExpectedMainSha.Trim().ToLowerInvariant()

    Write-Host "===== EXACT-MAIN NATIVE MERGETREE RENAME DIAGNOSTIC ====="
    if (git status --porcelain) {
        throw "Working tree must be clean before native MergeTree rename diagnosis."
    }
    $branch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
        throw "Native MergeTree rename diagnosis must run from local main."
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
    if (git status --porcelain) {
        throw "Working tree changed during exact-main gate."
    }
    Write-Host "EXACT_MAIN_NATIVE_MERGETREE_RENAME_OK"

    Write-Host "`n===== REQUIRED SERVICES / GLOBAL IDLE / ZERO WORKER ====="
    foreach ($service in @("postgres", "clickhouse")) {
        $running = @(& docker compose ps --status running -q $service | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($LASTEXITCODE -ne 0 -or $running.Count -ne 1) {
            throw "$service must have exactly one running Compose container."
        }
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "stop-idle-worker.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Global Data Engine idle gate failed."
    }
    $workerRunning = @(& docker compose ps --status running -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect running worker containers."
    }
    $workerAll = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect all worker containers."
    }
    Write-Host "worker_running_count=$($workerRunning.Count)"
    Write-Host "worker_container_count_all_states=$($workerAll.Count)"
    if ($workerRunning.Count -ne 0 -or $workerAll.Count -ne 0) {
        throw "No worker container in any state is allowed during native MergeTree rename diagnosis."
    }
    Write-Host "ZERO_WORKER_CONTAINERS_OK"

    $clickhouseId = @(& docker compose ps --status running -q clickhouse | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })[0].Trim()
    $healthLines = @(& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $clickhouseId 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect ClickHouse health."
    }
    $health = @($healthLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($health.Count -ne 1 -or $health[0].Trim().ToLowerInvariant() -ne "healthy") {
        throw "ClickHouse must be Docker-health healthy before native MergeTree rename diagnosis."
    }
    Write-Host "clickhouse_docker_health=healthy"

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

    function Get-SchemaVersionPath {
        $rows = @(& docker compose exec -T clickhouse clickhouse-client --query "SELECT arrayStringConcat(data_paths, ';') FROM system.tables WHERE database = 'markorbit_facts' AND name = 'schema_version' FORMAT TSVRaw")
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to resolve schema_version data path."
        }
        $values = @($rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($values.Count -ne 1) {
            throw "Expected exactly one schema_version data path."
        }
        $path = ($values[0].Split(';')[0]).Trim()
        if ($path -notmatch '^/var/lib/clickhouse/[A-Za-z0-9_./-]+/$') {
            throw "Resolved schema_version path is unsafe."
        }
        return $path
    }

    function Get-TmpInsertNames([string]$DataPath) {
        if ($DataPath -notmatch '^/var/lib/clickhouse/[A-Za-z0-9_./-]+/$') {
            throw "Refusing to inspect an unsafe ClickHouse data path."
        }
        $rows = @(& docker compose exec -T clickhouse sh -lc "find '$DataPath' -maxdepth 1 -mindepth 1 -type d -name 'tmp_insert_*' -printf '%f\n' 2>/dev/null || true")
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect tmp_insert directories."
        }
        return @($rows | Where-Object { $_ -match '^tmp_insert_[A-Za-z0-9_-]+$' } | Sort-Object)
    }

    Write-Host "`n===== PRE-PROBE BUSINESS / ACTIVITY EVIDENCE ====="
    $activeQueries = [int64](Invoke-ClickHouseScalar "SELECT count() FROM system.processes WHERE query NOT LIKE '%system.processes%'")
    $unfinishedMutations = [int64](Invoke-ClickHouseScalar "SELECT count() FROM system.mutations WHERE is_done = 0")
    Write-Host "clickhouse_active_queries=$activeQueries"
    Write-Host "clickhouse_unfinished_mutations=$unfinishedMutations"
    if ($activeQueries -ne 0 -or $unfinishedMutations -ne 0) {
        throw "ClickHouse is not idle enough for a disposable native MergeTree rename probe."
    }

    $schemaPath = Get-SchemaVersionPath
    $schemaSnapshotBefore = Invoke-ClickHouseScalar "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL"
    $schemaTmpBefore = @(Get-TmpInsertNames $schemaPath)
    Write-Host "schema_version_snapshot_before=$schemaSnapshotBefore"
    Write-Host "schema_version_tmp_insert_count_before=$($schemaTmpBefore.Count)"
    foreach ($name in $schemaTmpBefore) {
        Write-Host "schema_version_tmp_before=$name"
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmssfff"
    $probeDatabase = "markorbit_native_rename_probe_$timestamp"
    $probeTable = "merge_tree_probe"
    if ($probeDatabase -notmatch '^markorbit_native_rename_probe_[0-9]{8}_[0-9]{9}$') {
        throw "Generated probe database name is invalid."
    }

    $evidenceDir = Join-Path $EvidenceRoot "clickhouse_native_merge_tree_rename_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path
    $reportPath = Join-Path $evidenceDir "native_merge_tree_rename.json"

    $probeCreated = $false
    $insertSucceeded = $false
    $cleanupSucceeded = $false
    $insertExitCode = -1
    $insertOutput = @()
    $probePath = ""
    $probeUuid = ""
    $probeTmp = @()
    $probeParts = @()
    $probeRowCount = -1

    try {
        Write-Host "`n===== CREATE DISPOSABLE MERGETREE PROBE ====="
        & docker compose exec -T clickhouse clickhouse-client --query "CREATE DATABASE $probeDatabase"
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to create disposable probe database."
        }
        $probeCreated = $true
        & docker compose exec -T clickhouse clickhouse-client --query "CREATE TABLE $probeDatabase.$probeTable (probe_id UInt64, payload String) ENGINE = MergeTree ORDER BY probe_id"
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to create disposable MergeTree probe table."
        }

        $identityRows = @(& docker compose exec -T clickhouse clickhouse-client --query "SELECT toString(uuid), arrayStringConcat(data_paths, ';') FROM system.tables WHERE database = '$probeDatabase' AND name = '$probeTable' FORMAT TSVRaw")
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to resolve disposable probe table identity/path."
        }
        $identityRows = @($identityRows | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($identityRows.Count -ne 1) {
            throw "Expected exactly one disposable probe table identity row."
        }
        $identityParts = $identityRows[0] -split "`t", 2
        if ($identityParts.Count -ne 2) {
            throw "Disposable probe identity query returned an unexpected shape."
        }
        $probeUuid = $identityParts[0].Trim().ToLowerInvariant()
        $probePath = ($identityParts[1].Split(';')[0]).Trim()
        if ($probeUuid -notmatch '^[0-9a-f-]{36}$' -or $probePath -notmatch '^/var/lib/clickhouse/[A-Za-z0-9_./-]+/$') {
            throw "Resolved disposable probe identity/path is unsafe."
        }
        Write-Host "probe_database=$probeDatabase"
        Write-Host "probe_table=$probeTable"
        Write-Host "probe_uuid=$probeUuid"
        Write-Host "probe_path=$probePath"

        Write-Host "`n===== ONE-ROW NATIVE MERGETREE INSERT / PART COMMIT ====="
        $savedErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $insertOutput = @(& docker compose exec -T clickhouse clickhouse-client --query "INSERT INTO $probeDatabase.$probeTable VALUES (1, 'native-part-rename-probe')" 2>&1)
            $insertExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $savedErrorActionPreference
        }
        foreach ($line in $insertOutput) {
            if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
                Write-Host "probe_insert_output=$line"
            }
        }
        Write-Host "probe_insert_exit_code=$insertExitCode"
        $insertSucceeded = ($insertExitCode -eq 0)
        Write-Host "probe_insert_succeeded=$insertSucceeded"

        if ($probePath) {
            $probeTmp = @(Get-TmpInsertNames $probePath)
            $probePartLines = @(& docker compose exec -T clickhouse clickhouse-client --query "SELECT name, active, rows, bytes_on_disk FROM system.parts WHERE database = '$probeDatabase' AND table = '$probeTable' ORDER BY name FORMAT TSVRaw")
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to capture disposable probe system.parts evidence."
            }
            $probeParts = @($probePartLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
            Write-Host "probe_tmp_insert_count=$($probeTmp.Count)"
            foreach ($name in $probeTmp) {
                Write-Host "probe_tmp_insert=$name"
            }
            foreach ($part in $probeParts) {
                Write-Host "probe_part=$part"
            }
        }

        if ($insertSucceeded) {
            $probeRowCount = [int64](Invoke-ClickHouseScalar "SELECT count() FROM $probeDatabase.$probeTable")
            Write-Host "probe_row_count=$probeRowCount"
            if ($probeRowCount -ne 1 -or $probeTmp.Count -ne 0 -or $probeParts.Count -lt 1) {
                throw "Disposable MergeTree insert returned success but durable part evidence is inconsistent."
            }
        }
    }
    finally {
        if ($probeCreated) {
            Write-Host "`n===== NORMAL SQL PROBE CLEANUP ====="
            $savedErrorActionPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = "Continue"
                $dropOutput = @(& docker compose exec -T clickhouse clickhouse-client --query "DROP DATABASE IF EXISTS $probeDatabase SYNC" 2>&1)
                $dropExitCode = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $savedErrorActionPreference
            }
            foreach ($line in $dropOutput) {
                if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
                    Write-Host "probe_cleanup_output=$line"
                }
            }
            $cleanupSucceeded = ($dropExitCode -eq 0)
            Write-Host "probe_cleanup_exit_code=$dropExitCode"
            Write-Host "probe_cleanup_succeeded=$cleanupSucceeded"
        }
    }

    Write-Host "`n===== POST-PROBE BUSINESS INVARIANTS ====="
    $schemaSnapshotAfter = Invoke-ClickHouseScalar "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL"
    $schemaTmpAfter = @(Get-TmpInsertNames $schemaPath)
    $businessSnapshotPreserved = ($schemaSnapshotAfter -eq $schemaSnapshotBefore)
    Write-Host "schema_version_snapshot_after=$schemaSnapshotAfter"
    Write-Host "schema_version_snapshot_preserved=$businessSnapshotPreserved"
    Write-Host "schema_version_tmp_insert_count_after=$($schemaTmpAfter.Count)"
    foreach ($name in $schemaTmpAfter) {
        Write-Host "schema_version_tmp_after=$name"
    }
    if (-not $businessSnapshotPreserved) {
        throw "schema_version logical snapshot changed during disposable diagnosis."
    }

    $classification = if ($insertSucceeded) {
        "NATIVE_MERGETREE_RENAME_PASS_SCHEMA_VERSION_SPECIFIC_SUSPECT"
    }
    else {
        "NATIVE_MERGETREE_RENAME_FAIL_ACTIVE_HOT_BIND_SUSPECT"
    }

    $report = [ordered]@{
        report_version = "CLICKHOUSE_NATIVE_MERGETREE_RENAME_DIAGNOSTIC_V1"
        engine_sha = $head
        clickhouse_health = "healthy"
        worker_running_count = $workerRunning.Count
        worker_container_count_all_states = $workerAll.Count
        pre_probe = [ordered]@{
            active_queries = $activeQueries
            unfinished_mutations = $unfinishedMutations
            schema_version_path = $schemaPath
            schema_version_snapshot = $schemaSnapshotBefore
            schema_version_tmp_insert_dirs = @($schemaTmpBefore)
        }
        probe = [ordered]@{
            database = $probeDatabase
            table = $probeTable
            uuid = $probeUuid
            data_path = $probePath
            insert_exit_code = $insertExitCode
            insert_succeeded = $insertSucceeded
            insert_output = @($insertOutput | ForEach-Object { [string]$_ })
            tmp_insert_dirs = @($probeTmp)
            parts = @($probeParts)
            row_count = $probeRowCount
            cleanup_succeeded = $cleanupSucceeded
        }
        post_probe = [ordered]@{
            schema_version_snapshot = $schemaSnapshotAfter
            schema_version_snapshot_preserved = $businessSnapshotPreserved
            schema_version_tmp_insert_dirs = @($schemaTmpAfter)
        }
        classification = $classification
        manual_filesystem_cleanup_performed = $false
        permission_repair_performed = $false
        schema_apply_performed = $false
        corpus_replay_performed = $false
        worker_start_performed = $false
        worker_stop_performed = $false
    }
    $report | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $reportPath

    Write-Host "`n===== DIAGNOSTIC RESULT ====="
    Write-Host "classification=$classification"
    Write-Host "manual_filesystem_cleanup_performed=False"
    Write-Host "permission_repair_performed=False"
    Write-Host "schema_apply_performed=False"
    Write-Host "corpus_replay_performed=False"
    Write-Host "worker_start_performed=False"
    Write-Host "worker_stop_performed=False"
    Write-Host "Report: $reportPath"
    Write-Host "CLICKHOUSE_NATIVE_MERGETREE_RENAME_DIAGNOSTIC_COMPLETE"

    if (-not $cleanupSucceeded) {
        throw "Disposable probe cleanup did not complete through normal ClickHouse SQL. No manual filesystem cleanup was attempted."
    }
}
finally {
    Pop-Location
}
