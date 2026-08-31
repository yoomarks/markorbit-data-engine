param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not $OutputPath) {
        $OutputPath = Join-Path "reports" "clickhouse_active_hot_permission_$((Get-Date).ToString('yyyyMMdd_HHmmss')).json"
    }

    Write-Host "===== ACTIVE HOT STORAGE CONTRACT ====="
    $storageContractPath = Join-Path (Split-Path -Parent $OutputPath) "active_hot_storage_contract.json"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "assert-clickhouse-active-hot-storage-contract.ps1") `
        -OutputPath $storageContractPath
    if ($LASTEXITCODE -ne 0) {
        throw "Active Hot storage contract failed before permission diagnostics."
    }
    Write-Host "ACTIVE_HOT_STORAGE_CONTRACT_OK"

    Write-Host "===== ACTIVE HOT PERMISSION DIAGNOSTIC V2 ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "diagnose-clickhouse-active-hot-permissions.ps1") `
        -OutputPath $OutputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Base active-Hot permission diagnostic failed."
    }
    if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "Base active-Hot permission report was not created."
    }

    $report = Get-Content -LiteralPath $OutputPath -Raw | ConvertFrom-Json
    if ($report.report_version -ne "CLICKHOUSE_ACTIVE_HOT_PERMISSION_DIAGNOSTIC_V1") {
        throw "Unexpected base permission report version."
    }

    $clickhouseId = docker compose ps --status running -q clickhouse
    if ($LASTEXITCODE -ne 0 -or -not $clickhouseId) {
        throw "ClickHouse must remain running while comparison evidence is collected."
    }
    $worker = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect persistent worker state during comparison evidence collection."
    }
    if ($worker) {
        throw "Persistent worker must remain stopped while comparison evidence is collected."
    }

    $serverUid = [int64]$report.server_identity.uid
    $serverGid = [int64]$report.server_identity.gid
    $serverIdentity = "${serverUid}:${serverGid}"

    function Get-ContainerStat([string]$Path) {
        if ($Path -notmatch '^/var/lib/clickhouse(?:/[A-Za-z0-9_./-]*)?/?$') {
            throw "Refusing to stat an unexpected ClickHouse path: $Path"
        }
        $lines = @(& docker compose exec -T clickhouse sh -lc "stat -c '%u|%g|%a|%A|%n' '$Path'")
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to stat ClickHouse comparison path: $Path"
        }
        $matches = @($lines | Where-Object { $_ -match '^\d+\|\d+\|[0-7]+\|' })
        if ($matches.Count -ne 1) {
            throw "Unexpected stat result for ClickHouse comparison path: $Path"
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

    if ($null -eq $report.cn_comparison) {
        throw "No CN comparison table was resolved by the base diagnostic."
    }
    $cnPath = [string]$report.cn_comparison.data_path
    if ($cnPath -notmatch '^/var/lib/clickhouse/[A-Za-z0-9_./-]+/$') {
        throw "CN comparison data path is unsafe or unexpected: $cnPath"
    }

    $cnPathStat = Get-ContainerStat $cnPath
    $cnRwx = $false
    & docker compose exec -T --user $serverIdentity clickhouse sh -lc "test -r '$cnPath' && test -w '$cnPath' && test -x '$cnPath'"
    if ($LASTEXITCODE -eq 0) {
        $cnRwx = $true
    }

    $report.cn_comparison | Add-Member -NotePropertyName path_stat -NotePropertyValue ([pscustomobject]$cnPathStat) -Force
    $report.cn_comparison | Add-Member -NotePropertyName rwx_for_server_identity -NotePropertyValue $cnRwx -Force
    $report | Add-Member -NotePropertyName comparison_evidence_version -NotePropertyValue "CN_COMPARISON_PATH_V1" -Force
    $report | Add-Member -NotePropertyName storage_contract_report -NotePropertyValue $storageContractPath -Force
    $report | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "`n===== SCHEMA_VERSION PATH STATS ====="
    foreach ($stat in @($report.schema_version.path_stats)) {
        Write-Host ("schema_path_stat|uid={0}|gid={1}|mode={2}|permissions={3}|path={4}" -f `
            $stat.uid, $stat.gid, $stat.mode, $stat.permissions, $stat.path)
    }

    Write-Host "`n===== CN COMPARISON PATH EVIDENCE ====="
    Write-Host "cn_comparison_table=$($report.cn_comparison.table)"
    Write-Host "cn_comparison_uuid=$($report.cn_comparison.uuid)"
    Write-Host "cn_comparison_path=$cnPath"
    Write-Host "cn_comparison_path_stat|uid=$($cnPathStat.uid)|gid=$($cnPathStat.gid)|mode=$($cnPathStat.mode)|permissions=$($cnPathStat.permissions)|path=$($cnPathStat.path)"
    Write-Host "cn_comparison_rwx_for_server_identity=$cnRwx"

    Write-Host "`n===== TMP INSERT EVIDENCE ====="
    $tmpStats = @($report.schema_version.tmp_insert_dirs)
    Write-Host "schema_version_tmp_insert_dirs=$($tmpStats.Count)"
    foreach ($stat in $tmpStats) {
        Write-Host ("schema_tmp_insert_stat|uid={0}|gid={1}|mode={2}|permissions={3}|path={4}" -f `
            $stat.uid, $stat.gid, $stat.mode, $stat.permissions, $stat.path)
    }

    Write-Host "comparison_evidence_version=CN_COMPARISON_PATH_V1"
    Write-Host "storage_contract_report=$storageContractPath"
    Write-Host "Repair attempted: False"
    Write-Host "Safe to apply schema: False"
    Write-Host "Report: $OutputPath"
    Write-Host "ACTIVE_HOT_PERMISSION_DIAGNOSTIC_V2_COMPLETE"
}
finally {
    Pop-Location
}
