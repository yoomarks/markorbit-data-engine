param(
    [string]$AcceptedVolume = "markorbit-data-engine_clickhouse_data",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $ids = @(& docker compose ps --status running -q clickhouse 2>$null |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
        throw "Exactly one running ClickHouse container is required."
    }
    $cid = $ids[0].Trim()
    $health = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $cid 2>$null).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect ClickHouse health." }
    $mountsJson = (& docker inspect --format '{{json .Mounts}}' $cid 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($mountsJson)) {
        throw "Unable to inspect ClickHouse mounts."
    }
    $mounts = $mountsJson | ConvertFrom-Json
    $matches = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    if ($matches.Count -ne 1) { throw "Expected exactly one /var/lib/clickhouse mount." }
    $mount = $matches[0]
    $type = [string]$mount.Type
    $name = [string]$mount.Name
    $source = [string]$mount.Source
    $rw = [bool]$mount.RW

    $tmpLines = @(& docker exec $cid sh -c "find /var/lib/clickhouse/store/771/7716c662-1886-4e4b-a7e2-631c80ac8dd2 -maxdepth 1 -type d -name 'tmp_insert_*' -printf '.\\n' | wc -l" 2>$null)
    if ($LASTEXITCODE -ne 0 -or $tmpLines.Count -eq 0) { throw "Unable to inspect schema_version tmp_insert state." }
    $tmpCount = [int64]$tmpLines[-1].Trim()

    $blockers = @()
    if ($health -ne 'healthy') { $blockers += 'CLICKHOUSE_NOT_HEALTHY' }
    if ($type -ne 'volume') { $blockers += 'ACTIVE_CLICKHOUSE_DATA_NOT_LINUX_VOLUME' }
    if ($name -ne $AcceptedVolume) { $blockers += 'ACTIVE_CLICKHOUSE_DATA_VOLUME_NOT_ACCEPTED' }
    if (-not $rw) { $blockers += 'ACTIVE_CLICKHOUSE_DATA_NOT_RW' }
    if ($tmpCount -ne 0) { $blockers += 'SCHEMA_VERSION_TMP_INSERT_PRESENT' }

    $report = [ordered]@{
        report_version = 'CLICKHOUSE_ACTIVE_DATA_STORAGE_CONTRACT_V2'
        accepted_volume = $AcceptedVolume
        actual_mount_type = $type
        actual_mount_name = $name
        actual_mount_source = $source
        mount_rw = $rw
        clickhouse_health = $health
        schema_version_tmp_insert_count = $tmpCount
        blockers = @($blockers)
        safe_for_clickhouse_merge_tree_writes = ($blockers.Count -eq 0)
        windows_host_bind_accepted = $false
        filesystem_mutation_performed = $false
        compose_mutation_performed = $false
        schema_apply_performed = $false
        corpus_replay_performed = $false
    }
    if ($OutputPath) {
        $parent = Split-Path -Parent $OutputPath
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        $report | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $OutputPath
    }

    Write-Host "accepted_clickhouse_data_volume=$AcceptedVolume"
    Write-Host "actual_clickhouse_data_mount_type=$type"
    Write-Host "actual_clickhouse_data_volume=$name"
    Write-Host "actual_clickhouse_data_source=$source"
    Write-Host "active_clickhouse_data_rw=$rw"
    Write-Host "clickhouse_health=$health"
    Write-Host "schema_version_tmp_insert_count=$tmpCount"
    Write-Host "active_data_storage_blockers=$($blockers.Count)"
    foreach ($blocker in $blockers) { Write-Host "active_data_storage_blocker=$blocker" }
    Write-Host "windows_host_bind_accepted=False"
    Write-Host "filesystem_mutation_performed=False"
    Write-Host "compose_mutation_performed=False"
    Write-Host "schema_apply_performed=False"
    Write-Host "corpus_replay_performed=False"

    if ($blockers.Count -ne 0) {
        throw "Active ClickHouse data storage contract is not accepted: $($blockers -join ', ')"
    }
    Write-Host "CLICKHOUSE_ACTIVE_DATA_STORAGE_CONTRACT_PASS"
}
finally {
    Pop-Location
}
