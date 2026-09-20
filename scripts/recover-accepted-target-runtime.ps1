[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$PlanPath = '',
    [string]$EvidenceRoot = 'D:\yoomarks\governed-plans\769',
    [switch]$Apply,
    [string]$AuthorityToken = '',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:Issue = 769
$script:Operation = 'accepted-target-runtime-recovery-r1'
$script:Distro = 'MarkOrbit-ClickHouse'
$script:RuntimeRoot = 'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse'
$script:HttpPort = 28123
$script:NativePort = 29000
$script:ClickHouseVersion = '24.8.14.39'
$script:ConfigPath = '/opt/markorbit-clickhouse-production/config.xml'
$script:UsersPath = '/opt/markorbit-clickhouse-production/users.xml'
$script:ConfigSha = 'c7240b6c05a96dff2dc4c9e5a801cd524065bd101b5d006f2e8610b63ca56a59'
$script:UsersSha = '16b281607c47f9ee1f1bd8e3d09c4fc556320e833f17d05b597dec78aa2eb233'
$script:HotVhdx = 'D:\MarkOrbitData\production\clickhouse\hot_us.vhdx'
$script:HotName = 'markorbit_prod_hot_us'
$script:HotMount = '/mnt/wsl/markorbit_prod_hot_us'
$script:HotPath = '/mnt/wsl/markorbit_prod_hot_us/clickhouse-data/'
$script:HotUuid = '521a7b20-4380-4d6a-8018-2bab78fc2c4b'
[long]$script:HotBytes = 274877906944
$script:WarmVhdx = 'E:\MarkOrbitData\production\clickhouse\warm_cn.vhdx'
$script:WarmName = 'markorbit_prod_warm_cn'
$script:WarmMount = '/mnt/wsl/markorbit_prod_warm_cn'
$script:WarmPath = '/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/'
$script:WarmUuid = '2ee74d16-f0bd-461b-ab6a-279603e6c570'
[long]$script:WarmBytes = 842887331840
$script:Database = 'markorbit_facts'

function Require([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Invoke-Native {
    param([string]$Command, [string[]]$Arguments, [switch]$AllowFailure)
    $old = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $out = @(& $Command @Arguments 2>&1)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    $lines = @($out | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $code -ne 0) {
        throw "$Command failed ($code): $($lines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$code; lines=$lines }
}

function Get-Sha([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Assert-ExactMain {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Require ($head -eq $expected -and $origin -eq $expected) 'Exact main drift.'
    Require (-not (git status --porcelain=v1)) 'Working tree is not clean.'
}

function Get-RuntimeRegistry {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    $rows = @()
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
        $item = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if ($item -and $item.DistributionName -eq $script:Distro) { $rows += $item }
    }
    Require ($rows.Count -eq 1) 'Accepted runtime distro registration is not exact-one.'
    $base = [string]$rows[0].BasePath
    if ($base.StartsWith('\\?\')) { $base = $base.Substring(4) }
    $actual = [IO.Path]::GetFullPath($base).TrimEnd('\').ToLowerInvariant()
    $expected = [IO.Path]::GetFullPath($script:RuntimeRoot).TrimEnd('\').ToLowerInvariant()
    Require ($actual -eq $expected) 'Accepted runtime root drifted.'
    return [ordered]@{ name=$script:Distro; root=$script:RuntimeRoot }
}

function Get-RunningDistros {
    $r = Invoke-Native 'wsl.exe' @('--list','--running','--quiet') -AllowFailure
    if ($r.exit_code -ne 0) { return @() }
    return @(
        $r.lines |
            ForEach-Object { $_.Replace([string][char]0, '').Trim() } |
            Where-Object { $_ }
    )
}

function Invoke-Runtime([string]$Command, [switch]$AllowFailure) {
    return Invoke-Native 'wsl.exe' @('-d',$script:Distro,'-u','root','--','bash','-lc',$Command) -AllowFailure:$AllowFailure
}

function Runtime-SingleLine([string]$Command, [string]$Label) {
    $r = Invoke-Runtime $Command
    Require ($r.lines.Count -eq 1) "$Label did not return exactly one line."
    return $r.lines[0].Trim()
}

function Get-MountFact([string]$MountPoint) {
    $q = "[ -d '$MountPoint' ] && findmnt -n -o SOURCE,FSTYPE --target '$MountPoint'"
    $r = Invoke-Runtime $q -AllowFailure
    if ($r.exit_code -ne 0 -or $r.lines.Count -eq 0) { return $null }
    $fields = @($r.lines[0].Trim() -split '\s+')
    Require ($fields.Count -ge 2) "Mount fact malformed for $MountPoint."
    $device = $fields[0]
    $uuid = Runtime-SingleLine "blkid -s UUID -o value '$device'" "UUID $MountPoint"
    [long]$bytes = Runtime-SingleLine "blockdev --getsize64 '$device'" "size $MountPoint"
    return [ordered]@{
        mount=$MountPoint; device=$device; fstype=$fields[1];
        uuid=$uuid; virtual_bytes=$bytes
    }
}

function Test-NamedMountVisible([string]$MountPoint,[string[]]$RunningDistros) {
    foreach ($distro in $RunningDistros) {
        $q = "[ -d '$MountPoint' ] && findmnt -n --target '$MountPoint' >/dev/null"
        $r = Invoke-Native 'wsl.exe' @('-d',$distro,'-u','root','--','sh','-lc',$q) -AllowFailure
        if ($r.exit_code -eq 0) { return $true }
    }
    return $false
}

function Get-TargetHealth {
    try {
        $uri = "http://127.0.0.1:$($script:HttpPort)/?query=SELECT%20version()"
        $version = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri $uri).Content.Trim()
        return [ordered]@{ ready=($version -eq $script:ClickHouseVersion); version=$version }
    } catch {
        return [ordered]@{ ready=$false; version=$null }
    }
}

function Start-Keeper {
    return Start-Process -FilePath 'wsl.exe' -ArgumentList @(
        '-d',$script:Distro,'-u','root','--','tail','-f','/dev/null'
    ) -WindowStyle Hidden -PassThru
}

function Start-TargetServer {
    $pidFile = '/opt/markorbit-clickhouse-production/server.pid'
    $command = "set -eu; rm -f '$pidFile'; clickhouse server --config-file='$($script:ConfigPath)' --daemon --pid-file='$pidFile'"
    $r = Invoke-Runtime $command
    Require ($r.exit_code -eq 0) 'Accepted target server start failed.'
    $pid = Runtime-SingleLine "test -s '$pidFile' && cat '$pidFile'" 'accepted target server pid'
    Require ($pid -match '^[0-9]+$') 'Accepted target server pid is invalid.'
    $alive = Invoke-Runtime "kill -0 '$pid'" -AllowFailure
    Require ($alive.exit_code -eq 0) 'Accepted target server exited immediately after daemon launch.'
}
function Query-Target([string]$Sql) {
    $escaped = [uri]::EscapeDataString($Sql)
    $uri = "http://127.0.0.1:$($script:HttpPort)/?query=$escaped"
    return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri $uri).Content.Trim()
}

function Validate-Mount([object]$Fact,[string]$Uuid,[long]$Bytes,[string]$Label) {
    Require ($null -ne $Fact) "$Label mount is absent."
    Require ([string]$Fact.fstype -eq 'ext4') "$Label filesystem is not ext4."
    Require ([string]$Fact.uuid -eq $Uuid) "$Label UUID drifted."
    Require ([long]$Fact.virtual_bytes -eq $Bytes) "$Label virtual size drifted."
}

function Validate-Target {
    $health = Get-TargetHealth
    Require ([bool]$health.ready) 'Accepted target ClickHouse is not healthy.'
    $disks = Query-Target "SELECT name,path FROM system.disks WHERE name IN ('hot_us','warm_cn') ORDER BY name FORMAT TSV"
    Require ($disks -match 'hot_us\s+/mnt/wsl/markorbit_prod_hot_us/clickhouse-data/') 'hot_us disk path drifted.'
    Require ($disks -match 'warm_cn\s+/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/') 'warm_cn disk path drifted.'
    $policies = Query-Target "SELECT policy_name,arrayStringConcat(disks, ',') FROM system.storage_policies WHERE policy_name IN ('hot_us_only','warm_cn_only') ORDER BY policy_name FORMAT TSV"
    Require ($policies -match 'hot_us_only\s+hot_us') 'hot_us_only policy drifted.'
    Require ($policies -match 'warm_cn_only\s+warm_cn') 'warm_cn_only policy drifted.'
    $sql = "SELECT count() FROM system.parts WHERE active AND database='$($script:Database)' AND disk_name NOT IN ('hot_us','warm_cn')"
    $unexpected = Query-Target $sql
    Require ([int64]$unexpected -eq 0) 'Unexpected accepted-target active-part placement.'
    return [ordered]@{ health=$health; disks=$disks; policies=$policies; unexpected_parts=0 }
}

function Write-Json([object]$Value,[string]$Path) {
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Path -Encoding UTF8
}

if ($ContractOnly) {
    Write-Host 'ACCEPTED_TARGET_RUNTIME_RECOVERY_CONTRACT_OK'
    exit 0
}

Assert-ExactMain
$runtime = Get-RuntimeRegistry
Require (Test-Path -LiteralPath $script:HotVhdx -PathType Leaf) 'Accepted hot_us VHDX missing.'
Require (Test-Path -LiteralPath $script:WarmVhdx -PathType Leaf) 'Accepted warm_cn VHDX missing.'
$hotFile = Get-Item -LiteralPath $script:HotVhdx
$warmFile = Get-Item -LiteralPath $script:WarmVhdx

$runningDistros = @(Get-RunningDistros)
$targetRunning = $runningDistros -contains $script:Distro
$hotVisible = Test-NamedMountVisible $script:HotMount $runningDistros
$warmVisible = Test-NamedMountVisible $script:WarmMount $runningDistros
$health = Get-TargetHealth
$hotMountFact = $null
$warmMountFact = $null
if ($targetRunning) {
    $hotMountFact = Get-MountFact $script:HotMount
    $warmMountFact = Get-MountFact $script:WarmMount
}
$state = if ($health.ready -and $targetRunning -and $hotMountFact -and $warmMountFact) { 'HEALTHY_ALREADY' }
    elseif (-not $health.ready -and -not $hotVisible -and -not $warmVisible) { 'OFFLINE_UNMOUNTED' }
    else { 'PARTIAL_REVIEW_REQUIRED' }
if ($state -eq 'HEALTHY_ALREADY') {
    Validate-Mount $hotMountFact $script:HotUuid $script:HotBytes 'hot_us'
    Validate-Mount $warmMountFact $script:WarmUuid $script:WarmBytes 'warm_cn'
    $null = Validate-Target
}

if (-not $Apply) {
    Require ($state -ne 'PARTIAL_REVIEW_REQUIRED') 'Target is partially recovered; manual review required.'
    $dir = Join-Path $EvidenceRoot 'review'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $plan = [ordered]@{
        version=1; issue=$script:Issue; operation_id=$script:Operation;
        engine_sha=$ExpectedMainSha.ToLowerInvariant(); state=$state;
        runtime=$runtime; runtime_running=$targetRunning;
        running_distros=@($runningDistros); clickhouse_version=$script:ClickHouseVersion;
        expected_config_sha256=$script:ConfigSha; expected_users_sha256=$script:UsersSha;
        hot_us=[ordered]@{
            vhdx=$script:HotVhdx; physical_length=[long]$hotFile.Length;
            last_write_utc=$hotFile.LastWriteTimeUtc.ToString('o');
            mount_name=$script:HotName; mount_path=$script:HotMount;
            uuid=$script:HotUuid; virtual_bytes=$script:HotBytes
        };
        warm_cn=[ordered]@{
            vhdx=$script:WarmVhdx; physical_length=[long]$warmFile.Length;
            last_write_utc=$warmFile.LastWriteTimeUtc.ToString('o');
            mount_name=$script:WarmName; mount_path=$script:WarmMount;
            uuid=$script:WarmUuid; virtual_bytes=$script:WarmBytes
        };
        apply=[ordered]@{
            mount_only_existing_vhdx=$true; start_existing_server=$true;
            ddl=$false; insert=$false; replay=$false; format=$false; resize=$false;
            unmount=$false; shutdown=$false; unregister=$false
        }
    }
    $target = if ($PlanPath) { $PlanPath } else { Join-Path $dir 'accepted_target_runtime_recovery_plan.json' }
    Write-Json $plan $target
    $sha = Get-Sha $target
    Write-Host "plan_path=$target"
    Write-Host "plan_sha256=$sha"
    Write-Host "state=$state"
    Write-Host "authority_token=GO #769 TARGET-RUNTIME $sha RECOVER"
    Write-Host 'mutation_performed=False'
    exit 0
}

Require (-not [string]::IsNullOrWhiteSpace($PlanPath)) 'Apply requires -PlanPath.'
Require (Test-Path -LiteralPath $PlanPath -PathType Leaf) 'Frozen recovery plan missing.'
$planSha = Get-Sha $PlanPath
$expectedToken = "GO #769 TARGET-RUNTIME $planSha RECOVER"
Require ($AuthorityToken -ceq $expectedToken) 'Authority token does not match frozen recovery plan.'
$plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
Require ([string]$plan.engine_sha -eq $ExpectedMainSha.ToLowerInvariant()) 'Frozen plan engine SHA drifted.'
Require ([string]$plan.state -eq 'OFFLINE_UNMOUNTED') 'Apply is only valid from OFFLINE_UNMOUNTED.'
Require ([long]$plan.hot_us.physical_length -eq [long]$hotFile.Length) 'hot_us physical length drifted.'
Require ([long]$plan.warm_cn.physical_length -eq [long]$warmFile.Length) 'warm_cn physical length drifted.'
Require ([string]$plan.hot_us.last_write_utc -eq $hotFile.LastWriteTimeUtc.ToString('o')) 'hot_us mtime drifted.'
Require ([string]$plan.warm_cn.last_write_utc -eq $warmFile.LastWriteTimeUtc.ToString('o')) 'warm_cn mtime drifted.'
$admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
Require ($admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'Apply requires Administrator PowerShell.'

$applyDir = Join-Path $EvidenceRoot 'apply'
New-Item -ItemType Directory -Force -Path $applyDir | Out-Null
$journalPath = Join-Path $applyDir 'accepted_target_runtime_recovery_journal.json'
$journal = [ordered]@{
    version=1; plan_sha256=$planSha; engine_sha=$ExpectedMainSha.ToLowerInvariant();
    keeper_started=$false; hot_us_mounted=$false; warm_cn_mounted=$false;
    server_started=$false; validated=$false; last_error=$null
}
Write-Json $journal $journalPath

try {
    $keeper = Start-Keeper
    Start-Sleep -Milliseconds 800
    Require (-not $keeper.HasExited) 'Accepted runtime keeper exited unexpectedly.'
    $journal.keeper_started=$true
    $journal.keeper_host_pid=$keeper.Id
    Write-Json $journal $journalPath

    $configSha = Runtime-SingleLine "sha256sum '$($script:ConfigPath)' | cut -d' ' -f1" 'config sha'
    $usersSha = Runtime-SingleLine "sha256sum '$($script:UsersPath)' | cut -d' ' -f1" 'users sha'
    Require ($configSha -eq $script:ConfigSha) 'Accepted target config SHA drifted.'
    Require ($usersSha -eq $script:UsersSha) 'Accepted target users SHA drifted.'
    Require (-not (Get-TargetHealth).ready) 'Accepted target unexpectedly became healthy before recovery.'
    Require ($null -eq (Get-MountFact $script:HotMount)) 'hot_us mount appeared before authorized attach.'
    Require ($null -eq (Get-MountFact $script:WarmMount)) 'warm_cn mount appeared before authorized attach.'

    $mount = Invoke-Native 'wsl.exe' @('--mount','--vhd',$script:HotVhdx,'--name',$script:HotName)
    Require ($mount.exit_code -eq 0) 'hot_us named mount failed.'
    $journal.hot_us_mounted=$true
    Write-Json $journal $journalPath
    Start-Sleep -Milliseconds 800
    Validate-Mount (Get-MountFact $script:HotMount) $script:HotUuid $script:HotBytes 'hot_us'

    $mount = Invoke-Native 'wsl.exe' @('--mount','--vhd',$script:WarmVhdx,'--name',$script:WarmName)
    Require ($mount.exit_code -eq 0) 'warm_cn named mount failed.'
    $journal.warm_cn_mounted=$true
    Write-Json $journal $journalPath
    Start-Sleep -Milliseconds 800
    Validate-Mount (Get-MountFact $script:WarmMount) $script:WarmUuid $script:WarmBytes 'warm_cn'

    Start-TargetServer
    $journal.server_started=$true
    Write-Json $journal $journalPath
    $ready=$false
    for($i=0; $i -lt 60; $i++) {
        if ((Get-TargetHealth).ready) { $ready=$true; break }
        Start-Sleep -Milliseconds 500
    }
    Require $ready 'Accepted target did not become healthy.'
    $validation = Validate-Target
    $journal.validated=$true
    Write-Json $journal $journalPath

    $receiptPath = Join-Path $applyDir 'accepted_target_runtime_recovery_receipt.json'
    $receipt = [ordered]@{
        decision='ACCEPTED_TARGET_RUNTIME_RECOVERED';
        issue=$script:Issue; operation_id=$script:Operation;
        engine_sha=$ExpectedMainSha.ToLowerInvariant(); plan_sha256=$planSha;
        keeper_host_pid=$keeper.Id;
        hot_us=Get-MountFact $script:HotMount;
        warm_cn=Get-MountFact $script:WarmMount;
        target=$validation;
        mutation=[ordered]@{
            existing_vhdx_mounts=$true; server_start=$true;
            ddl=$false; insert=$false; replay=$false; format=$false; resize=$false;
            unmount=$false; shutdown=$false; unregister=$false
        };
        recovered_at=[DateTimeOffset]::UtcNow.ToString('o')
    }
    Write-Json $receipt $receiptPath
    Write-Host "receipt_path=$receiptPath"
    Write-Host "receipt_sha256=$(Get-Sha $receiptPath)"
    Write-Host 'decision=ACCEPTED_TARGET_RUNTIME_RECOVERED'
}
catch {
    $journal.last_error=$_.Exception.Message
    Write-Json $journal $journalPath
    Write-Host "journal_path=$journalPath"
    Write-Host 'automatic_unmount_or_rollback_performed=False'
    throw
}
