[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$CurrentHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$RetainedVolume = "markorbit-data-engine_clickhouse_data",
    [string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545",
    [int64]$ExpectedPreActivationActiveRows = 2948782201,
    [ValidateRange(10, 300)]
    [int]$MergeTreeProbeTimeoutSeconds = 30,
    [switch]$Execute
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

function Get-ComposeClickHouseIdAnyState {
    $ids = @(Invoke-DockerText -Arguments @('compose','ps','-a','-q','clickhouse') |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($ids.Count -ne 1) { throw "Expected exactly one Compose ClickHouse container; observed=$($ids.Count)" }
    return $ids[0]
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

function Get-StableRuntimeBaseline([string]$ContainerId) {
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
    if ($lines.Count -lt 6) { throw "Unexpected runtime baseline output: $($lines -join ' | ')" }
    $parts = $lines[1] -split "`t"
    if ($parts.Count -ne 4 -or $lines[2] -notmatch '^[0-9a-f]{64}$' -or $lines[3] -notmatch '^[0-9a-f]{64}$') {
        throw 'Unexpected runtime baseline shape.'
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

function Invoke-MergeTreeAcceptance([string]$ContainerId, [int]$TimeoutSeconds) {
    $db = "markorbit_linux_volume_reconcile_probe_$((Get-Date).ToString('yyyyMMdd_HHmmssfff'))"
    $queryId = $db
    $sql = "CREATE DATABASE $db; CREATE TABLE $db.t (id UInt64) ENGINE=MergeTree ORDER BY id; INSERT INTO $db.t VALUES (1); SELECT count() FROM $db.t; DROP DATABASE $db SYNC;"
    $payload = ConvertTo-Base64Utf8 $sql
    $script = "set -eu; printf %s $payload | timeout ${TimeoutSeconds}s clickhouse-client --user `"`$CLICKHOUSE_USER`" --password `"`$CLICKHOUSE_PASSWORD`" --query_id $queryId --multiquery"
    try {
        $out = @(Invoke-ContainerScript $ContainerId $script | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
        if ($out -notcontains '1') { throw "MergeTree probe did not return row_count=1: $($out -join ' | ')" }
    }
    catch {
        $cleanup = "timeout 15s clickhouse-client --user `"`$CLICKHOUSE_USER`" --password `"`$CLICKHOUSE_PASSWORD`" --multiquery --query `"KILL QUERY WHERE query_id = '$queryId' SYNC; DROP DATABASE IF EXISTS $db SYNC;`""
        try { Invoke-ContainerScript $ContainerId $cleanup | Out-Null } catch {}
        throw
    }
    return [ordered]@{ database = $db; query_id = $queryId; passed = $true }
}

try {
    Write-Host '===== POST-COPY LINUX-VOLUME RECONCILIATION ====='
    if (git status --porcelain) { throw 'Working tree must be clean.' }
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Reconciliation must run from main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed.' }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw 'Exact-main mismatch.' }

    Write-Host 'reconcile_stage=global_idle_zero_worker'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop-idle-worker.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Global idle gate failed.' }
    $workerAll = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($workerAll.Count -ne 0) { throw 'Worker containers must be absent.' }

    $oldCid = Get-ComposeClickHouseIdAnyState
    $oldImage = ((Invoke-DockerText -Arguments @('inspect','--format','{{.Config.Image}}',$oldCid)) -join '').Trim()
    if ($oldImage -notmatch ':24\.8(?:$|[.-])') { throw "Unexpected ClickHouse image: $oldImage" }
    $oldHealth = ((Invoke-DockerText -Arguments @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$oldCid)) -join '').Trim()
    $oldMounts = (((Invoke-DockerText -Arguments @('inspect','--format','{{json .Mounts}}',$oldCid)) -join '').Trim() | ConvertFrom-Json)
    $oldHot = Get-SingleMountByDestination $oldMounts '/var/lib/clickhouse'
    Write-Host "current_clickhouse_health=$oldHealth"
    Write-Host "current_clickhouse_mount_type=$($oldHot.Type)"
    Write-Host "current_clickhouse_mount_source=$($oldHot.Source)"

    $expectedSource = Normalize-WindowsPath $CurrentHotPath
    if ([string]$oldHot.Type -eq 'bind') {
        $actualSource = Normalize-WindowsPath ([string]$oldHot.Source)
        if (-not [string]::Equals($actualSource,$expectedSource,[System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Current bind source drifted: actual=$actualSource expected=$expectedSource"
        }
    }
    elseif ([string]$oldHot.Type -eq 'volume' -and [string]$oldHot.Name -eq $RetainedVolume) {
        Write-Host 'current_state=ALREADY_ON_RETAINED_LINUX_VOLUME'
    }
    else {
        throw "Unexpected current ClickHouse data mount: type=$($oldHot.Type) name=$($oldHot.Name) source=$($oldHot.Source)"
    }

    @(Invoke-DockerText -Arguments @('volume','inspect',$RetainedVolume)) | Out-Null
    $sourceIdentity = Get-StaticIdentityFromMount "type=bind,source=$expectedSource,target=/root,readonly" $oldImage 'source-bind'
    Write-Host "source_metadata_sha256=$($sourceIdentity.metadata_sha256)"
    Write-Host "source_store_uuid_sha256=$($sourceIdentity.store_uuid_sha256)"

    if (-not $Execute) {
        Write-Host 'execute=False'
        Write-Host 'volume_wipe_performed=False'
        Write-Host 'copy_performed=False'
        Write-Host 'windows_bind_rollback_performed=False'
        Write-Host 'POST_COPY_LINUX_VOLUME_RECONCILIATION_READY'
        return
    }

    Write-Host 'reconcile_stage=activate_existing_linux_volume'
    Invoke-DockerText -Arguments @('compose','-f','docker-compose.yml','stop','clickhouse') | Out-Null
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to refresh origin/main before activation.' }
    $head2 = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin2 = (git rev-parse origin/main).Trim().ToLowerInvariant()
    if ($head2 -ne $expected -or $origin2 -ne $expected -or (git status --porcelain)) { throw 'Exact-main drift before Linux-volume activation.' }

    Invoke-DockerText -Arguments @('compose','-f','docker-compose.yml','up','-d','--wait','--no-deps','--force-recreate','clickhouse') | Out-Null
    $newCid = Get-RunningClickHouseId
    $newHealth = ((Invoke-DockerText -Arguments @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$newCid)) -join '').Trim()
    if ($newHealth -ne 'healthy') { throw "Linux-volume ClickHouse did not become healthy: $newHealth" }
    $newMounts = (((Invoke-DockerText -Arguments @('inspect','--format','{{json .Mounts}}',$newCid)) -join '').Trim() | ConvertFrom-Json)
    $newHot = Get-SingleMountByDestination $newMounts '/var/lib/clickhouse'
    if ([string]$newHot.Type -ne 'volume' -or [string]$newHot.Name -ne $RetainedVolume -or -not [bool]$newHot.RW) {
        throw "Linux-volume activation mismatch: type=$($newHot.Type) name=$($newHot.Name) rw=$($newHot.RW)"
    }

    Write-Host 'reconcile_stage=freeze_merges_and_validate_identity'
    Invoke-ContainerScript $newCid "clickhouse-client --user `"`$CLICKHOUSE_USER`" --password `"`$CLICKHOUSE_PASSWORD`" --query 'SYSTEM STOP MERGES'" | Out-Null
    $mergesStopped = $true
    try {
        $targetIdentity = Get-StaticIdentityFromContainer $newCid
        Assert-StaticIdentityEqual $sourceIdentity $targetIdentity
        Write-Host "linux_volume_metadata_sha256=$($targetIdentity.metadata_sha256)"
        Write-Host "linux_volume_store_uuid_sha256=$($targetIdentity.store_uuid_sha256)"
        Write-Host 'STATIC_METADATA_UUID_IDENTITY_OK'

        $baseline = Get-StableRuntimeBaseline $newCid
        if ($baseline.schema_snapshot -ne $ExpectedSchemaSnapshot) { throw "schema snapshot drifted: $($baseline.schema_snapshot)" }
        if ([int64]$baseline.nondefault_storage_policy_count -ne 0) { throw 'Non-default ClickHouse storage policy detected.' }
        if ([int64]$baseline.suspicious_detached_part_count -ne 0) { throw "Suspicious detached/broken parts detected: $($baseline.suspicious_detached_part_count)" }
        if ([int64]$baseline.active_rows -gt $ExpectedPreActivationActiveRows) {
            throw "Active physical rows unexpectedly increased without writers: before=$ExpectedPreActivationActiveRows current=$($baseline.active_rows)"
        }
        $rowReduction = $ExpectedPreActivationActiveRows - [int64]$baseline.active_rows

        $tmpRaw = @(Invoke-ContainerScript $newCid "find /var/lib/clickhouse/store/771/7716c662-1886-4e4b-a7e2-631c80ac8dd2 -maxdepth 1 -type d -name 'tmp_insert_*' -printf '.\\n' | wc -l")
        $tmpCount = [int64]$tmpRaw[-1].Trim()
        if ($tmpCount -ne 0) { throw "schema_version tmp_insert dirs remain on Linux volume: $tmpCount" }

        Write-Host 'reconcile_stage=native_mergetree_acceptance'
        $probe = Invoke-MergeTreeAcceptance $newCid $MergeTreeProbeTimeoutSeconds

        Write-Host '===== POST-COPY LINUX-VOLUME RECONCILIATION RESULT ====='
        Write-Host "active_clickhouse_data_mount_type=$($newHot.Type)"
        Write-Host "active_clickhouse_data_volume=$($newHot.Name)"
        Write-Host "schema_version_snapshot=$($baseline.schema_snapshot)"
        Write-Host "active_table_count=$($baseline.active_table_count)"
        Write-Host "active_rows=$($baseline.active_rows)"
        Write-Host "physical_row_reduction_since_pre_activation=$rowReduction"
        Write-Host "table_uuid_sha256=$($baseline.table_uuid_sha256)"
        Write-Host "table_engine_sha256=$($baseline.table_engine_sha256)"
        Write-Host 'suspicious_detached_part_count=0'
        Write-Host 'schema_version_tmp_insert_dirs=0'
        Write-Host 'native_mergetree_commit_verified=True'
        Write-Host "native_mergetree_probe_database=$($probe.database)"
        Write-Host 'volume_wipe_performed=False'
        Write-Host 'copy_performed=False'
        Write-Host 'windows_bind_rollback_performed=False'
        Write-Host 'windows_hot_bind_active=False'
        Write-Host 'schema_apply_performed=False'
        Write-Host 'corpus_replay_performed=False'
        Write-Host 'POST_COPY_LINUX_VOLUME_RECONCILIATION_PASS'
    }
    finally {
        if ($mergesStopped) {
            try { Invoke-ContainerScript $newCid "clickhouse-client --user `"`$CLICKHOUSE_USER`" --password `"`$CLICKHOUSE_PASSWORD`" --query 'SYSTEM START MERGES'" | Out-Null } catch {}
        }
    }
}
catch {
    Write-Host 'POST_COPY_LINUX_VOLUME_RECONCILIATION_FAILURE'
    Write-Host "exception_type=$($_.Exception.GetType().FullName)"
    Write-Host "exception_message=$($_.Exception.Message)"
    Write-Host 'automatic_windows_bind_rollback_performed=False'
    throw
}
finally {
    Pop-Location
}
