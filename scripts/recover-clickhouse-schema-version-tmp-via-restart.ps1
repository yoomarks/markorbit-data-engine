param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string]$ExpectedSchemaUuid = "7716c662-1886-4e4b-a7e2-631c80ac8dd2",
    [string[]]$ExpectedTmpDirs = @(
        "tmp_insert_all_1_1_0",
        "tmp_insert_all_2_2_0",
        "tmp_insert_all_3_3_0"
    ),
    [string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545",
    [ValidateRange(30, 1800)]
    [int]$ClickHouseHealthTimeoutSeconds = 600,
    [string]$EvidenceRoot = "reports"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$clickhouseStoppedByOperator = $false
$restartPerformed = $false
$alreadyRecovered = $false
Push-Location $repoRoot
try {
    $expectedSha = $ExpectedMainSha.Trim().ToLowerInvariant()
    $expectedUuid = $ExpectedSchemaUuid.Trim().ToLowerInvariant()
    $expectedTmp = @($ExpectedTmpDirs | Sort-Object)

    Write-Host "===== EXACT-MAIN CLICKHOUSE TMP RECOVERY GATE ====="
    if (git status --porcelain) {
        throw "Working tree must be clean before ClickHouse tmp recovery."
    }
    $branch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
        throw "ClickHouse tmp recovery must run from local main."
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
    Write-Host "EXACT_MAIN_CLICKHOUSE_TMP_RECOVERY_OK"

    Write-Host "`n===== REQUIRED SERVICES AND GLOBAL IDLE ====="
    foreach ($service in @("postgres", "clickhouse")) {
        $running = docker compose ps --status running -q $service
        if ($LASTEXITCODE -ne 0 -or -not $running) {
            throw "$service must be running before ClickHouse tmp recovery."
        }
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "stop-idle-worker.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Global Data Engine idle gate failed."
    }
    $allWorkerContainers = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect worker containers."
    }
    Write-Host "worker_container_count_all_states=$($allWorkerContainers.Count)"
    if ($allWorkerContainers.Count -ne 0) {
        throw "No worker container in any state is allowed during ClickHouse tmp recovery."
    }
    Write-Host "ZERO_WORKER_CONTAINERS_OK"

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

    function Get-SchemaIdentityAndPath {
        $rows = @(& docker compose exec -T clickhouse clickhouse-client --query "SELECT toString(uuid), arrayStringConcat(data_paths, ';') FROM system.tables WHERE database = 'markorbit_facts' AND name = 'schema_version' FORMAT TSVRaw")
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to resolve schema_version identity/path."
        }
        $rows = @($rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($rows.Count -ne 1) {
            throw "Expected exactly one schema_version table row."
        }
        $parts = $rows[0] -split "`t", 2
        if ($parts.Count -ne 2) {
            throw "schema_version identity/path query returned an unexpected shape."
        }
        $uuid = $parts[0].Trim().ToLowerInvariant()
        $path = ($parts[1].Split(';')[0]).Trim()
        if ($uuid -notmatch '^[0-9a-f-]{36}$' -or $path -notmatch '^/var/lib/clickhouse/[A-Za-z0-9_./-]+/$') {
            throw "Resolved schema_version identity/path is unsafe."
        }
        return [pscustomobject]@{ uuid = $uuid; path = $path }
    }

    function Get-TmpInsertNames([string]$SchemaPath) {
        if ($SchemaPath -notmatch '^/var/lib/clickhouse/[A-Za-z0-9_./-]+/$') {
            throw "Refusing to inspect an unsafe schema_version path."
        }
        $rows = @(& docker compose exec -T clickhouse sh -lc "find '$SchemaPath' -maxdepth 1 -mindepth 1 -type d -name 'tmp_insert_*' -printf '%f\n' 2>/dev/null || true")
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect schema_version tmp_insert directories."
        }
        $names = @($rows | Where-Object { $_ -match '^tmp_insert_[A-Za-z0-9_-]+$' } | Sort-Object)
        return $names
    }

    function Get-RunningClickHouseContainerId {
        $ids = @(& docker compose ps --status running -q clickhouse 2>$null | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($LASTEXITCODE -ne 0) {
            return ""
        }
        if ($ids.Count -gt 1) {
            throw "Expected at most one running ClickHouse container during recovery."
        }
        if ($ids.Count -eq 1) {
            return $ids[0].Trim()
        }
        return ""
    }

    function Show-ClickHouseStartupDiagnostics([string]$Phase, [string]$ContainerId) {
        Write-Host "`n===== CLICKHOUSE STARTUP DIAGNOSTICS ====="
        Write-Host "clickhouse_health_phase=$Phase"
        $savedErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            if ($ContainerId) {
                $stateLines = @(& docker inspect --format 'state={{.State.Status}}|running={{.State.Running}}|exit_code={{.State.ExitCode}}|health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|started_at={{.State.StartedAt}}|finished_at={{.State.FinishedAt}}|restart_count={{.RestartCount}}' $ContainerId 2>&1)
                foreach ($line in $stateLines) {
                    if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
                        Write-Host "clickhouse_container_state=$line"
                    }
                }
                $healthLines = @(& docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}{{println .End "|" .ExitCode "|" .Output}}{{end}}{{end}}' $ContainerId 2>&1)
                foreach ($line in $healthLines) {
                    if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
                        Write-Host "clickhouse_health_log=$line"
                    }
                }
            }
            $logLines = @(& docker compose logs --tail 120 --no-color clickhouse 2>&1)
            foreach ($line in $logLines) {
                if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
                    Write-Host "clickhouse_startup_log=$line"
                }
            }
        }
        finally {
            $ErrorActionPreference = $savedErrorActionPreference
        }
        Write-Host "CLICKHOUSE_STARTUP_DIAGNOSTICS_COMPLETE"
    }

    function Wait-ClickHouseHealthy([string]$Phase) {
        $lastHealth = "not-running"
        $lastContainerId = ""
        $maxAttempts = [int][Math]::Ceiling($ClickHouseHealthTimeoutSeconds / 2.0)
        for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
            $containerId = Get-RunningClickHouseContainerId
            if ($containerId) {
                $lastContainerId = $containerId
                $healthLines = @(& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $containerId 2>$null)
                if ($LASTEXITCODE -eq 0) {
                    $healthValues = @($healthLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
                    if ($healthValues.Count -eq 1) {
                        $lastHealth = $healthValues[0].Trim().ToLowerInvariant()
                        if ($lastHealth -eq "healthy") {
                            Write-Host "clickhouse_health_phase=$Phase"
                            Write-Host "clickhouse_docker_health=healthy"
                            return $containerId
                        }
                    }
                }
            }
            Start-Sleep -Seconds 2
        }
        Show-ClickHouseStartupDiagnostics -Phase $Phase -ContainerId $lastContainerId
        throw "ClickHouse did not become Docker-health healthy during $Phase. last_health=$lastHealth"
    }

    Write-Host "`n===== CLICKHOUSE PREFLIGHT HEALTH ====="
    $null = Wait-ClickHouseHealthy -Phase "preflight"
    Write-Host "CLICKHOUSE_PREFLIGHT_HEALTHY_OK"

    Write-Host "`n===== PRE-RESTART TABLE/TMP EVIDENCE ====="
    $clickhouseVersion = Invoke-ClickHouseScalar "SELECT version()"
    Write-Host "clickhouse_version=$clickhouseVersion"
    if ($clickhouseVersion -notmatch '^24\.8\.') {
        throw "This recovery operator is frozen to ClickHouse 24.8.x."
    }

    $identity = Get-SchemaIdentityAndPath
    Write-Host "schema_version_uuid=$($identity.uuid)"
    Write-Host "schema_version_path=$($identity.path)"
    if ($identity.uuid -ne $expectedUuid) {
        throw "schema_version UUID drift detected."
    }

    $unfinishedMutations = [int64](Invoke-ClickHouseScalar "SELECT count() FROM system.mutations WHERE database = 'markorbit_facts' AND table = 'schema_version' AND is_done = 0")
    $activeQueries = [int64](Invoke-ClickHouseScalar "SELECT count() FROM system.processes WHERE query NOT LIKE '%system.processes%'")
    $schemaSnapshotBefore = Invoke-ClickHouseScalar "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL"
    Write-Host "schema_version_unfinished_mutations=$unfinishedMutations"
    Write-Host "clickhouse_active_queries=$activeQueries"
    Write-Host "schema_version_snapshot_current=$schemaSnapshotBefore"
    Write-Host "schema_version_snapshot_expected=$ExpectedSchemaSnapshot"
    if ($unfinishedMutations -ne 0 -or $activeQueries -ne 0) {
        throw "ClickHouse is not idle enough for a controlled native tmp recovery restart."
    }
    if ($schemaSnapshotBefore -ne $ExpectedSchemaSnapshot) {
        throw "schema_version logical snapshot drifted from the frozen pre-restart evidence."
    }

    $actualTmp = @(Get-TmpInsertNames $identity.path)
    Write-Host "tmp_insert_count_current=$($actualTmp.Count)"
    foreach ($name in $actualTmp) {
        Write-Host "tmp_current=$name"
    }

    if ($actualTmp.Count -eq 0) {
        $alreadyRecovered = $true
        Write-Host "recovery_mode=ALREADY_RECOVERED_AFTER_INTERRUPTED_RESTART"
        Write-Host "ZERO_TMP_ALREADY_RECOVERED_OK"
    }
    elseif ($actualTmp.Count -eq $expectedTmp.Count -and -not (Compare-Object -ReferenceObject $expectedTmp -DifferenceObject $actualTmp)) {
        Write-Host "recovery_mode=NATIVE_RESTART_REQUIRED"
        Write-Host "EXACT_TMP_SET_MATCH_OK"
    }
    else {
        throw "schema_version tmp_insert set is neither the frozen incident set nor the fully recovered zero-tmp state."
    }

    $partsBefore = @(& docker compose exec -T clickhouse clickhouse-client --query "SELECT name, active, rows, bytes_on_disk FROM system.parts WHERE database = 'markorbit_facts' AND table = 'schema_version' ORDER BY name FORMAT TSVRaw")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to capture schema_version part evidence."
    }
    foreach ($line in $partsBefore) {
        if (-not [string]::IsNullOrWhiteSpace($line)) {
            Write-Host "schema_part_current=$line"
        }
    }

    if (-not $alreadyRecovered) {
        Write-Host "`n===== CONTROLLED CLICKHOUSE-ONLY RESTART ====="
        & docker compose stop clickhouse | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to stop ClickHouse cleanly."
        }
        $clickhouseStoppedByOperator = $true
        $runningAfterStop = docker compose ps --status running -q clickhouse
        if ($LASTEXITCODE -ne 0 -or $runningAfterStop) {
            throw "ClickHouse is still running after controlled stop."
        }
        Write-Host "CLICKHOUSE_CONTROLLED_STOP_OK"

        & docker compose start clickhouse | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to start ClickHouse after controlled stop."
        }
        $null = Wait-ClickHouseHealthy -Phase "post-restart"
        $clickhouseStoppedByOperator = $false
        $restartPerformed = $true
        Write-Host "CLICKHOUSE_CONTROLLED_RESTART_READY_OK"
    }

    Write-Host "`n===== POST-RECOVERY NATIVE CLEANUP VERIFICATION ====="
    $versionAfter = Invoke-ClickHouseScalar "SELECT version()"
    $identityAfter = Get-SchemaIdentityAndPath
    if ($versionAfter -ne $clickhouseVersion) {
        throw "ClickHouse version changed across recovery verification."
    }
    if ($identityAfter.uuid -ne $expectedUuid -or $identityAfter.path -ne $identity.path) {
        throw "schema_version identity/path changed across recovery verification."
    }

    $tmpAfter = @(Get-TmpInsertNames $identityAfter.path)
    Write-Host "tmp_insert_count_after=$($tmpAfter.Count)"
    foreach ($name in $tmpAfter) {
        Write-Host "tmp_after=$name"
    }
    if ($tmpAfter.Count -ne 0) {
        throw "ClickHouse native startup cleanup did not remove every frozen tmp_insert directory. No manual filesystem cleanup was attempted."
    }

    $schemaSnapshotAfter = Invoke-ClickHouseScalar "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL"
    Write-Host "schema_version_snapshot_after=$schemaSnapshotAfter"
    if ($schemaSnapshotAfter -ne $ExpectedSchemaSnapshot) {
        throw "schema_version logical snapshot changed from the frozen pre-restart evidence."
    }

    $unfinishedMutationsAfter = [int64](Invoke-ClickHouseScalar "SELECT count() FROM system.mutations WHERE database = 'markorbit_facts' AND table = 'schema_version' AND is_done = 0")
    if ($unfinishedMutationsAfter -ne 0) {
        throw "schema_version has an unfinished mutation after recovery verification."
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $evidenceDir = Join-Path $EvidenceRoot "clickhouse_native_tmp_recovery_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path
    $permissionReport = Join-Path $evidenceDir "active_hot_permission_post_restart.json"

    Write-Host "`n===== POST-RECOVERY ACTIVE HOT V2 ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "diagnose-clickhouse-active-hot-permissions-v2.ps1") `
        -OutputPath $permissionReport
    if ($LASTEXITCODE -ne 0) {
        throw "Post-recovery active-Hot V2 diagnostic failed."
    }
    $report = Get-Content -LiteralPath $permissionReport -Raw | ConvertFrom-Json
    if ($report.report_version -ne "CLICKHOUSE_ACTIVE_HOT_PERMISSION_DIAGNOSTIC_V1") {
        throw "Unexpected post-recovery active-Hot report version."
    }
    if (@($report.blockers).Count -ne 0) {
        throw "Post-recovery active-Hot diagnostic still has blockers."
    }
    if (-not [bool]$report.schema_version.rwx_for_server_identity -or -not [bool]$report.disposable_root_rename_probe.passed -or -not [bool]$report.cn_comparison.rwx_for_server_identity) {
        throw "Post-recovery active-Hot ownership/rename comparison is not fully healthy."
    }

    Write-Host "`n===== RECOVERY RESULT ====="
    Write-Host "clickhouse_restart_performed=$restartPerformed"
    Write-Host "already_recovered_after_interrupted_restart=$alreadyRecovered"
    Write-Host "clickhouse_native_tmp_cleanup=True"
    Write-Host "manual_filesystem_cleanup_performed=False"
    Write-Host "permission_repair_performed=False"
    Write-Host "worker_start_performed=False"
    Write-Host "schema_apply_performed=False"
    Write-Host "corpus_replay_performed=False"
    Write-Host "post_recovery_permission_blockers=0"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host "CLICKHOUSE_NATIVE_TMP_RECOVERY_PASS"
}
finally {
    if ($clickhouseStoppedByOperator) {
        $runningIds = @(& docker compose ps --status running -q clickhouse 2>$null | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($LASTEXITCODE -eq 0 -and $runningIds.Count -gt 0) {
            Write-Host "ClickHouse is already running; fail-safe duplicate start is not needed."
        }
        else {
            Write-Host "Attempting fail-safe ClickHouse start after an interrupted recovery..."
            & docker compose start clickhouse | Out-Host
        }
    }
    Pop-Location
}