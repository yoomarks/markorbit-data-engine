param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $clickhouseId = docker compose ps --status running -q clickhouse
    if ($LASTEXITCODE -ne 0 -or -not $clickhouseId) {
        throw "ClickHouse must be running before the active-Hot permission diagnostic."
    }

    $worker = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect persistent worker state."
    }
    if ($worker) {
        throw "Persistent worker must be stopped before the active-Hot permission diagnostic."
    }

    $inspectRaw = @(& docker inspect $clickhouseId)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the running ClickHouse container."
    }
    $inspect = (($inspectRaw -join "`n") | ConvertFrom-Json)[0]
    $hotMounts = @($inspect.Mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse" })
    if ($hotMounts.Count -ne 1) {
        throw "Expected exactly one active /var/lib/clickhouse mount."
    }
    $hotMount = $hotMounts[0]
    if ($hotMount.Type -ne "bind" -or -not $hotMount.RW) {
        throw "Active ClickHouse Hot storage is not a writable bind mount."
    }

    Write-Host "Active Hot source: $($hotMount.Source)"
    Write-Host "Active Hot destination: $($hotMount.Destination)"
    Write-Host "Active Hot read/write: $($hotMount.RW)"

    $schemaRows = @(& docker compose exec -T clickhouse clickhouse-client --query "SELECT toString(uuid), arrayStringConcat(data_paths, ';') FROM system.tables WHERE database = 'markorbit_facts' AND name = 'schema_version' FORMAT TSVRaw")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve markorbit_facts.schema_version data path."
    }
    $schemaRows = @($schemaRows | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($schemaRows.Count -ne 1) {
        throw "Expected exactly one markorbit_facts.schema_version table row."
    }
    $schemaParts = $schemaRows[0] -split "`t", 2
    if ($schemaParts.Count -ne 2) {
        throw "schema_version data-path query returned an unexpected shape."
    }
    $schemaUuid = $schemaParts[0].Trim()
    $schemaPath = ($schemaParts[1].Split(';')[0]).Trim()
    if ($schemaUuid -notmatch '^[0-9a-fA-F-]{36}$' -or $schemaPath -notmatch '^/var/lib/clickhouse/[A-Za-z0-9_./-]+/$') {
        throw "Resolved schema_version identity/path is unsafe or unexpected."
    }

    $cnRows = @(& docker compose exec -T clickhouse clickhouse-client --query "SELECT name, toString(uuid), arrayStringConcat(data_paths, ';') FROM system.tables WHERE database = 'markorbit_facts' AND name LIKE 'cn_%' ORDER BY name LIMIT 1 FORMAT TSVRaw")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve a CN comparison table path."
    }
    $cnRows = @($cnRows | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $cnComparison = $null
    if ($cnRows.Count -eq 1) {
        $cnParts = $cnRows[0] -split "`t", 3
        if ($cnParts.Count -eq 3) {
            $cnComparison = [ordered]@{
                table = $cnParts[0].Trim()
                uuid = $cnParts[1].Trim()
                data_path = ($cnParts[2].Split(';')[0]).Trim()
            }
        }
    }

    $identityLines = @(& docker compose exec -T clickhouse sh -lc "stat -c '%u|%g' /proc/1")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve ClickHouse server process UID/GID."
    }
    $identityMatches = @($identityLines | Where-Object { $_ -match '^\d+\|\d+$' })
    if ($identityMatches.Count -ne 1) {
        throw "ClickHouse server UID/GID query returned an unexpected result."
    }
    $identityParts = $identityMatches[0].Split('|')
    $serverUid = [int64]$identityParts[0]
    $serverGid = [int64]$identityParts[1]
    $serverIdentity = "${serverUid}:${serverGid}"

    function Get-ContainerStat([string]$Path) {
        if ($Path -notmatch '^/var/lib/clickhouse/[A-Za-z0-9_./-]*/?$') {
            throw "Refusing to stat an unexpected ClickHouse path: $Path"
        }
        $lines = @(& docker compose exec -T clickhouse sh -lc "stat -c '%u|%g|%a|%A|%n' '$Path'")
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to stat ClickHouse path: $Path"
        }
        $matches = @($lines | Where-Object { $_ -match '^\d+\|\d+\|[0-7]+\|' })
        if ($matches.Count -ne 1) {
            throw "Unexpected stat result for ClickHouse path: $Path"
        }
        $parts = $matches[0] -split '\|', 5
        return [ordered]@{
            uid = [int64]$parts[0]
            gid = [int64]$parts[1]
            mode = $parts[2]
            permissions = $parts[3]
            path = $parts[4]
        }
    }

    $schemaParent = Split-Path -Parent $schemaPath.TrimEnd('/')
    $schemaPrefixParent = Split-Path -Parent $schemaParent
    $pathStats = @(
        Get-ContainerStat "/var/lib/clickhouse"
        Get-ContainerStat "/var/lib/clickhouse/store"
        Get-ContainerStat $schemaPrefixParent
        Get-ContainerStat $schemaParent
        Get-ContainerStat $schemaPath
    )

    $schemaWritable = $false
    & docker compose exec -T --user $serverIdentity clickhouse sh -lc "test -r '$schemaPath' && test -w '$schemaPath' && test -x '$schemaPath'"
    if ($LASTEXITCODE -eq 0) {
        $schemaWritable = $true
    }

    $tmpPaths = @(& docker compose exec -T clickhouse sh -lc "find '$schemaPath' -maxdepth 1 -mindepth 1 -type d -name 'tmp_insert_*' -print 2>/dev/null || true")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect stale schema_version tmp_insert directories."
    }
    $tmpPaths = @($tmpPaths | Where-Object { $_ -match '^/var/lib/clickhouse/[A-Za-z0-9_./-]+$' })
    $tmpStats = @()
    foreach ($tmpPath in $tmpPaths) {
        $tmpStats += Get-ContainerStat $tmpPath
    }

    $stamp = Get-Date -Format "yyyyMMddHHmmssfff"
    $probePath = "/var/lib/clickhouse/.markorbit-permission-probe-$stamp"
    $probeRenamedPath = "$probePath-renamed"
    $probeCommand = "set -eu; trap 'rm -rf `"$probePath`" `"$probeRenamedPath`"' EXIT; mkdir '$probePath'; printf probe > '$probePath/file'; mv '$probePath' '$probeRenamedPath'; test -f '$probeRenamedPath/file'; rm -rf '$probeRenamedPath'"
    $rootRenameProbe = $true
    $probeLines = @(& docker compose exec -T --user $serverIdentity clickhouse sh -lc $probeCommand 2>&1)
    $probeExit = $LASTEXITCODE
    if ($probeExit -ne 0) {
        $rootRenameProbe = $false
    }

    $blockers = @()
    if (-not $schemaWritable) {
        $blockers += "SCHEMA_VERSION_PATH_NOT_RWX_FOR_SERVER_UID"
    }
    if (-not $rootRenameProbe) {
        $blockers += "ACTIVE_HOT_ROOT_RENAME_PROBE_FAILED"
    }
    if ($tmpStats.Count -gt 0) {
        $blockers += "SCHEMA_VERSION_TMP_INSERT_PRESENT"
    }

    if (-not $OutputPath) {
        $OutputPath = Join-Path "reports" "clickhouse_active_hot_permission_$((Get-Date).ToString('yyyyMMdd_HHmmss')).json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }

    $report = [ordered]@{
        report_version = "CLICKHOUSE_ACTIVE_HOT_PERMISSION_DIAGNOSTIC_V1"
        status = if ($blockers.Count -eq 0) { "DIAGNOSTIC_COMPLETE" } else { "BLOCKED" }
        safe_to_apply_schema = $false
        next_action = "REVIEW_ACTIVE_HOT_PERMISSION_EVIDENCE"
        active_hot = [ordered]@{
            type = [string]$hotMount.Type
            source = [string]$hotMount.Source
            destination = [string]$hotMount.Destination
            rw = [bool]$hotMount.RW
        }
        server_identity = [ordered]@{
            uid = $serverUid
            gid = $serverGid
        }
        schema_version = [ordered]@{
            uuid = $schemaUuid
            data_path = $schemaPath
            rwx_for_server_identity = $schemaWritable
            path_stats = $pathStats
            tmp_insert_dirs = $tmpStats
        }
        cn_comparison = $cnComparison
        disposable_root_rename_probe = [ordered]@{
            passed = $rootRenameProbe
            exit_code = $probeExit
            output = @($probeLines | Select-Object -First 20)
        }
        blockers = $blockers
        repair_attempted = $false
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
    }

    $report | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "ClickHouse server UID:GID: $serverIdentity"
    Write-Host "schema_version UUID: $schemaUuid"
    Write-Host "schema_version path: $schemaPath"
    Write-Host "schema_version RWX for server identity: $schemaWritable"
    Write-Host "schema_version tmp_insert dirs: $($tmpStats.Count)"
    Write-Host "Disposable Hot-root rename probe: $rootRenameProbe"
    Write-Host "Diagnostic status: $($report.status)"
    if ($blockers.Count -gt 0) {
        Write-Host "Blockers: $($blockers -join ', ')"
    }
    Write-Host "Repair attempted: False"
    Write-Host "Safe to apply schema: False"
    Write-Host "Report: $OutputPath"
    Write-Host "ACTIVE_HOT_PERMISSION_DIAGNOSTIC_COMPLETE"
}
finally {
    Pop-Location
}
