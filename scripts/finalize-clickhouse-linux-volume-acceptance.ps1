[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$SourceHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$AcceptedVolume = "markorbit-data-engine_clickhouse_data",
    [string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545",
    [ValidateRange(10, 300)]
    [int]$MergeTreeProbeTimeoutSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-DockerText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& docker @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $rendered = @($output | ForEach-Object { $_.ToString() })
    if ($exitCode -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $exitCode`: $($rendered -join [Environment]::NewLine)"
    }
    return $rendered
}

function ConvertTo-Base64Utf8([string]$Text) {
    $normalized = $Text.Replace("`r`n", "`n").Replace("`r", "`n")
    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalized))
}

function New-QuoteSafeShellRunner([string]$Script) {
    $payload = ConvertTo-Base64Utf8 $Script
    return "printf %s $payload | base64 -d | sh"
}

function Invoke-DockerRunScript([string[]]$RunArguments, [string]$Image, [string]$Script) {
    $runner = New-QuoteSafeShellRunner $Script
    return Invoke-DockerText -Arguments (@('run') + $RunArguments + @('--entrypoint','sh',$Image,'-c',$runner))
}

function Invoke-ContainerScript([string]$ContainerId, [string]$Script) {
    $runner = New-QuoteSafeShellRunner $Script
    return Invoke-DockerText -Arguments @('exec',$ContainerId,'sh','-c',$runner)
}

function Normalize-WindowsPath([string]$Path) {
    $candidate = $Path.Replace('/', '\')
    if ($candidate -notmatch '^[A-Za-z]:\\') { throw "Expected absolute Windows path: $Path" }
    return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
}

function Get-SingleMountByDestination([object]$Mounts, [string]$Destination) {
    $result = $null
    foreach ($mount in $Mounts) {
        if ([string]$mount.Destination -ne $Destination) { continue }
        if ($null -ne $result) { throw "Multiple $Destination mounts found." }
        $result = $mount
    }
    if ($null -eq $result) { throw "Required mount missing: $Destination" }
    return $result
}

function Get-RunningClickHouseId {
    $ids = @(Invoke-DockerText -Arguments @('compose','ps','--status','running','-q','clickhouse') |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($ids.Count -ne 1) { throw "Expected exactly one running ClickHouse container; observed=$($ids.Count)" }
    return $ids[0]
}

function Get-StaticIdentityFromMount([string]$MountSpec, [string]$Image, [string]$Label) {
    $script = @'
set -eu
test -d /root/metadata
test -d /root/store
metadata_count="$(find /root/metadata -mindepth 1 \( -type f -o -type l \) -printf '.\n' | wc -l | tr -d ' ')"
metadata_sha="$(find /root/metadata -mindepth 1 \( -type d -printf 'D\t%P\n' -o -type f -printf 'F\t%P\t%s\n' -o -type l -printf 'L\t%P\t%l\n' \) | LC_ALL=C sort | sha256sum | awk '{print $1}')"
store_uuid_count="$(find /root/store -mindepth 2 -maxdepth 2 -type d -printf '.\n' | wc -l | tr -d ' ')"
store_uuid_sha="$(find /root/store -mindepth 2 -maxdepth 2 -type d -printf '%P\n' | LC_ALL=C sort | sha256sum | awk '{print $1}')"
printf '%s\t%s\t%s\t%s\n' "$metadata_count" "$metadata_sha" "$store_uuid_count" "$store_uuid_sha"
'@
    $line = @(Invoke-DockerRunScript @('--rm','--user','0:0','--mount',$MountSpec) $Image $script |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })[-1]
    $parts = $line -split "`t"
    if ($parts.Count -ne 4 -or $parts[1] -notmatch '^[0-9a-f]{64}$' -or $parts[3] -notmatch '^[0-9a-f]{64}$') {
        throw "Unexpected $Label static identity: $line"
    }
    return [ordered]@{
        metadata_entry_count = [int64]$parts[0]
        metadata_sha256 = $parts[1]
        store_uuid_count = [int64]$parts[2]
        store_uuid_sha256 = $parts[3]
    }
}

function Get-StaticIdentityFromContainer([string]$ContainerId) {
    $script = @'
set -eu
metadata_count="$(find /var/lib/clickhouse/metadata -mindepth 1 \( -type f -o -type l \) -printf '.\n' | wc -l | tr -d ' ')"
metadata_sha="$(find /var/lib/clickhouse/metadata -mindepth 1 \( -type d -printf 'D\t%P\n' -o -type f -printf 'F\t%P\t%s\n' -o -type l -printf 'L\t%P\t%l\n' \) | LC_ALL=C sort | sha256sum | awk '{print $1}')"
store_uuid_count="$(find /var/lib/clickhouse/store -mindepth 2 -maxdepth 2 -type d -printf '.\n' | wc -l | tr -d ' ')"
store_uuid_sha="$(find /var/lib/clickhouse/store -mindepth 2 -maxdepth 2 -type d -printf '%P\n' | LC_ALL=C sort | sha256sum | awk '{print $1}')"
printf '%s\t%s\t%s\t%s\n' "$metadata_count" "$metadata_sha" "$store_uuid_count" "$store_uuid_sha"
'@
    $line = @(Invoke-ContainerScript $ContainerId $script | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })[-1]
    $parts = $line -split "`t"
    if ($parts.Count -ne 4 -or $parts[1] -notmatch '^[0-9a-f]{64}$' -or $parts[3] -notmatch '^[0-9a-f]{64}$') {
        throw "Unexpected Linux-volume static identity: $line"
    }
    return [ordered]@{
        metadata_entry_count = [int64]$parts[0]
        metadata_sha256 = $parts[1]
        store_uuid_count = [int64]$parts[2]
        store_uuid_sha256 = $parts[3]
    }
}

function Assert-StaticIdentityEqual($Source, $Target) {
    foreach ($field in @('metadata_entry_count','metadata_sha256','store_uuid_count','store_uuid_sha256')) {
        if ([string]$Source[$field] -ne [string]$Target[$field]) {
            throw "Static identity mismatch $field`: source=$($Source[$field]) target=$($Target[$field])"
        }
    }
}

function Get-StableBaseline([string]$ContainerId) {
    $script = @'
set -eu
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL"
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT countDistinct(tuple(database, table)), count(), coalesce(sum(rows), 0), coalesce(sum(bytes_on_disk), 0) FROM system.parts WHERE active AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')"
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT database, name, toString(uuid) FROM system.tables WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') ORDER BY database, name" | sha256sum | awk '{print $1}'
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT database, name, engine FROM system.tables WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') ORDER BY database, name" | sha256sum | awk '{print $1}'
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT count() FROM system.tables WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') AND storage_policy != 'default'"
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT count() FROM system.detached_parts WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') AND match(lower(reason), 'broken|corrupt|unexpected|ignored')"
'@
    $lines = @(Invoke-ContainerScript $ContainerId $script | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($lines.Count -lt 6) { throw "Unexpected stable baseline output: $($lines -join ' | ')" }
    $parts = $lines[1] -split "`t"
    if ($parts.Count -ne 4 -or $lines[2] -notmatch '^[0-9a-f]{64}$' -or $lines[3] -notmatch '^[0-9a-f]{64}$') {
        throw 'Unexpected stable baseline shape.'
    }
    return [ordered]@{
        schema_snapshot = $lines[0]
        active_table_count = [int64]$parts[0]
        active_part_count = [int64]$parts[1]
        active_rows = [int64]$parts[2]
        active_bytes_on_disk = [int64]$parts[3]
        table_uuid_sha256 = $lines[2]
        table_engine_sha256 = $lines[3]
        nondefault_storage_policy_count = [int64]$lines[4]
        suspicious_detached_part_count = [int64]$lines[5]
    }
}

function Wait-ForMergesToDrain([string]$ContainerId) {
    $script = @'
set -eu
i=0
while [ "$i" -lt 30 ]; do
  n="$(clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT count() FROM system.merges")"
  if [ "$n" = "0" ]; then
    echo 0
    exit 0
  fi
  i=$((i+1))
  sleep 1
done
echo "MERGES_DID_NOT_DRAIN" >&2
exit 1
'@
    Invoke-ContainerScript $ContainerId $script | Out-Null
}

function Invoke-MergeTreeAcceptance([string]$ContainerId, [int]$TimeoutSeconds) {
    $db = "markorbit_linux_volume_final_probe_$((Get-Date).ToString('yyyyMMdd_HHmmssfff'))"
    $queryId = $db
    $sql = "CREATE DATABASE $db; CREATE TABLE $db.t (id UInt64) ENGINE=MergeTree ORDER BY id; INSERT INTO $db.t VALUES (1); SELECT count() FROM $db.t; DROP DATABASE $db SYNC;"
    $sqlPayload = ConvertTo-Base64Utf8 $sql
    $script = @'
set -eu
SQL_B64='__SQL_B64__'
printf '%s' "$SQL_B64" | base64 -d | timeout __TIMEOUT__s clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query_id '__QUERY_ID__' --multiquery
'@
    $script = $script.Replace('__SQL_B64__',$sqlPayload).Replace('__TIMEOUT__',[string]$TimeoutSeconds).Replace('__QUERY_ID__',$queryId)
    try {
        $out = @(Invoke-ContainerScript $ContainerId $script | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
        if ($out -notcontains '1') { throw "MergeTree probe did not return row_count=1: $($out -join ' | ')" }
    }
    catch {
        $cleanup = @'
set +e
timeout 15s clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --multiquery --query "KILL QUERY WHERE query_id = '__QUERY_ID__' SYNC; DROP DATABASE IF EXISTS __DB__ SYNC;"
exit 0
'@
        $cleanup = $cleanup.Replace('__QUERY_ID__',$queryId).Replace('__DB__',$db)
        try { Invoke-ContainerScript $ContainerId $cleanup | Out-Null } catch {}
        throw
    }
    return [ordered]@{ database = $db; query_id = $queryId; passed = $true }
}

try {
    Write-Host '===== LINUX-VOLUME FINAL ACCEPTANCE ====='
    if (git status --porcelain) { throw 'Working tree must be clean.' }
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Final acceptance must run from main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed.' }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw 'Exact-main mismatch.' }

    Write-Host 'finalize_stage=global_idle_zero_worker'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop-idle-worker.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Global idle gate failed.' }
    $workerAll = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($workerAll.Count -ne 0) { throw 'Worker containers must be absent.' }

    $cid = Get-RunningClickHouseId
    $health = ((Invoke-DockerText -Arguments @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$cid)) -join '').Trim()
    if ($health -ne 'healthy') { throw "ClickHouse must be healthy; observed=$health" }
    $image = ((Invoke-DockerText -Arguments @('inspect','--format','{{.Config.Image}}',$cid)) -join '').Trim()
    if ($image -notmatch ':24\.8(?:$|[.-])') { throw "Unexpected ClickHouse image: $image" }
    $mounts = (((Invoke-DockerText -Arguments @('inspect','--format','{{json .Mounts}}',$cid)) -join '').Trim() | ConvertFrom-Json)
    $hot = Get-SingleMountByDestination $mounts '/var/lib/clickhouse'
    if ([string]$hot.Type -ne 'volume' -or [string]$hot.Name -ne $AcceptedVolume -or -not [bool]$hot.RW) {
        throw "Accepted Linux-volume mount required: type=$($hot.Type) name=$($hot.Name) rw=$($hot.RW)"
    }

    Write-Host 'finalize_stage=freeze_merges_and_validate_identity'
    Invoke-ContainerScript $cid "clickhouse-client --user `"`$CLICKHOUSE_USER`" --password `"`$CLICKHOUSE_PASSWORD`" --query 'SYSTEM STOP MERGES'" | Out-Null
    $mergesStopped = $true
    try {
        Wait-ForMergesToDrain $cid
        $source = Normalize-WindowsPath $SourceHotPath
        $sourceIdentity = Get-StaticIdentityFromMount "type=bind,source=$source,target=/root,readonly" $image 'source-bind'
        $targetIdentity = Get-StaticIdentityFromContainer $cid
        Assert-StaticIdentityEqual $sourceIdentity $targetIdentity
        Write-Host "source_metadata_sha256=$($sourceIdentity.metadata_sha256)"
        Write-Host "linux_volume_metadata_sha256=$($targetIdentity.metadata_sha256)"
        Write-Host "source_store_uuid_sha256=$($sourceIdentity.store_uuid_sha256)"
        Write-Host "linux_volume_store_uuid_sha256=$($targetIdentity.store_uuid_sha256)"
        Write-Host 'STATIC_METADATA_UUID_IDENTITY_OK'

        $before = Get-StableBaseline $cid
        if ($before.schema_snapshot -ne $ExpectedSchemaSnapshot) { throw "schema snapshot drifted: $($before.schema_snapshot)" }
        if ([int64]$before.nondefault_storage_policy_count -ne 0) { throw 'Non-default ClickHouse storage policy detected.' }
        if ([int64]$before.suspicious_detached_part_count -ne 0) { throw "Suspicious detached/broken parts detected: $($before.suspicious_detached_part_count)" }
        $tmpRaw = @(Invoke-ContainerScript $cid "find /var/lib/clickhouse/store/771/7716c662-1886-4e4b-a7e2-631c80ac8dd2 -maxdepth 1 -type d -name 'tmp_insert_*' -printf '.\\n' | wc -l")
        $tmpCount = [int64]$tmpRaw[-1].Trim()
        if ($tmpCount -ne 0) { throw "schema_version tmp_insert dirs remain on Linux volume: $tmpCount" }

        Write-Host 'finalize_stage=native_mergetree_acceptance'
        $probe = Invoke-MergeTreeAcceptance $cid $MergeTreeProbeTimeoutSeconds

        $after = Get-StableBaseline $cid
        foreach ($field in @('schema_snapshot','active_table_count','active_rows','table_uuid_sha256','table_engine_sha256','nondefault_storage_policy_count','suspicious_detached_part_count')) {
            if ([string]$before[$field] -ne [string]$after[$field]) {
                throw "Stable baseline changed during final acceptance $field`: before=$($before[$field]) after=$($after[$field])"
            }
        }
        $targetIdentityAfter = Get-StaticIdentityFromContainer $cid
        Assert-StaticIdentityEqual $sourceIdentity $targetIdentityAfter

        Write-Host '===== LINUX-VOLUME FINAL ACCEPTANCE RESULT ====='
        Write-Host "active_clickhouse_data_mount_type=$($hot.Type)"
        Write-Host "active_clickhouse_data_volume=$($hot.Name)"
        Write-Host "schema_version_snapshot=$($after.schema_snapshot)"
        Write-Host "active_table_count=$($after.active_table_count)"
        Write-Host "active_rows=$($after.active_rows)"
        Write-Host "table_uuid_sha256=$($after.table_uuid_sha256)"
        Write-Host "table_engine_sha256=$($after.table_engine_sha256)"
        Write-Host 'suspicious_detached_part_count=0'
        Write-Host 'schema_version_tmp_insert_dirs=0'
        Write-Host 'native_mergetree_commit_verified=True'
        Write-Host "native_mergetree_probe_database=$($probe.database)"
        Write-Host 'source_bind_mutation_performed=False'
        Write-Host 'volume_wipe_performed=False'
        Write-Host 'copy_performed=False'
        Write-Host 'windows_bind_rollback_performed=False'
        Write-Host 'windows_hot_bind_active=False'
        Write-Host 'schema_apply_performed=False'
        Write-Host 'corpus_replay_performed=False'
        Write-Host 'CLICKHOUSE_LINUX_VOLUME_FINAL_ACCEPTANCE_PASS'
    }
    finally {
        if ($mergesStopped) {
            try { Invoke-ContainerScript $cid "clickhouse-client --user `"`$CLICKHOUSE_USER`" --password `"`$CLICKHOUSE_PASSWORD`" --query 'SYSTEM START MERGES'" | Out-Null } catch {}
        }
    }
}
catch {
    Write-Host 'CLICKHOUSE_LINUX_VOLUME_FINAL_ACCEPTANCE_FAILURE'
    Write-Host "exception_type=$($_.Exception.GetType().FullName)"
    Write-Host "exception_message=$($_.Exception.Message)"
    Write-Host 'automatic_windows_bind_rollback_performed=False'
    throw
}
finally {
    Pop-Location
}
