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
$script:Operation = 'accepted-target-runtime-resume-r1'
$script:Distro = 'MarkOrbit-ClickHouse'
$script:HttpPort = 28123
$script:NativePort = 29000
$script:Version = '24.8.14.39'
$script:ConfigPath = '/opt/markorbit-clickhouse-production/config.xml'
$script:UsersPath = '/opt/markorbit-clickhouse-production/users.xml'
$script:PidPath = '/opt/markorbit-clickhouse-production/server.pid'
$script:ConfigSha = 'c7240b6c05a96dff2dc4c9e5a801cd524065bd101b5d006f2e8610b63ca56a59'
$script:UsersSha = '16b281607c47f9ee1f1bd8e3d09c4fc556320e833f17d05b597dec78aa2eb233'
$script:OriginalPlanPath = 'D:\yoomarks\governed-plans\769\accepted-target-runtime-recovery-r2.json'
$script:OriginalPlanSha = '8717454aad7b0887230dd5274c397de75a8b6cf3b5b362955aa31e78ba4e03db'
$script:PriorJournalPath = 'D:\yoomarks\governed-plans\769\apply\accepted_target_runtime_recovery_journal.json'
$script:HotMount = '/mnt/wsl/markorbit_prod_hot_us'
$script:HotUuid = '521a7b20-4380-4d6a-8018-2bab78fc2c4b'
[long]$script:HotBytes = 274877906944
$script:WarmMount = '/mnt/wsl/markorbit_prod_warm_cn'
$script:WarmUuid = '2ee74d16-f0bd-461b-ab6a-279603e6c570'
[long]$script:WarmBytes = 842887331840

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

function Write-Json([object]$Value,[string]$Path) {
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Path -Encoding UTF8
}
function Assert-ExactMain {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Require ($head -eq $expected -and $origin -eq $expected) 'Exact main drift.'
    Require (-not (git status --porcelain=v1)) 'Working tree is not clean.'
}

function Invoke-Runtime([string]$Command,[switch]$AllowFailure) {
    return Invoke-Native 'wsl.exe' @('-d',$script:Distro,'-u','root','--','bash','-lc',$Command) -AllowFailure:$AllowFailure
}

function Runtime-SingleLine([string]$Command,[string]$Label) {
    $r = Invoke-Runtime $Command
    Require ($r.lines.Count -eq 1) "$Label did not return exactly one line."
    return $r.lines[0].Trim()
}

function Get-MountFact([string]$MountPoint) {
    $r = Invoke-Runtime "[ -d '$MountPoint' ] && findmnt -n -o SOURCE,FSTYPE --target '$MountPoint'" -AllowFailure
    if ($r.exit_code -ne 0 -or $r.lines.Count -eq 0) { return $null }
    $fields = @($r.lines[0].Trim() -split '\s+')
    Require ($fields.Count -ge 2) "Mount fact malformed for $MountPoint."
    $device = $fields[0]
    $uuid = Runtime-SingleLine "blkid -s UUID -o value '$device'" "UUID $MountPoint"
    [long]$bytes = Runtime-SingleLine "blockdev --getsize64 '$device'" "size $MountPoint"
    return [ordered]@{ mount=$MountPoint; device=$device; fstype=$fields[1]; uuid=$uuid; virtual_bytes=$bytes }
}

function Validate-Mount([object]$Fact,[string]$Uuid,[long]$Bytes,[string]$Label) {
    Require ($null -ne $Fact) "$Label mount is absent."
    Require ([string]$Fact.fstype -eq 'ext4') "$Label filesystem is not ext4."
    Require ([string]$Fact.uuid -eq $Uuid) "$Label UUID drifted."
    Require ([long]$Fact.virtual_bytes -eq $Bytes) "$Label virtual size drifted."
}
function Get-KeeperFact {
    $journal = Get-Content -LiteralPath $script:PriorJournalPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Require ([bool]$journal.keeper_started) 'Prior journal does not prove keeper start.'
    Require ([bool]$journal.hot_us_mounted -and [bool]$journal.warm_cn_mounted) 'Prior journal does not prove both mounts.'
    Require ([bool]$journal.server_started -and -not [bool]$journal.validated) 'Prior journal is not at server-attempt boundary.'
    Require ([string]$journal.last_error -eq 'Accepted target did not become healthy.') 'Prior journal failure reason drifted.'
    [int]$pid = $journal.keeper_host_pid
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pid" -ErrorAction SilentlyContinue
    Require ($null -ne $process) 'Accepted runtime keeper process is absent.'
    Require ([string]$process.Name -match '^wsl(\.exe)?$') 'Keeper PID is no longer a wsl process.'

    $running = Invoke-Native 'wsl.exe' @('--list','--running','--quiet')
    $runningDistros = @(
        $running.lines |
            ForEach-Object { $_.Replace([string][char]0, '').Trim() } |
            Where-Object { $_ }
    )
    Require ($runningDistros -contains $script:Distro) 'Accepted target distro is not currently running.'

    $commandLine = [string]$process.CommandLine
    $commandLineVisible = -not [string]::IsNullOrWhiteSpace($commandLine)
    if ($commandLineVisible) {
        Require ($commandLine -match 'MarkOrbit-ClickHouse') 'Keeper command line no longer targets accepted distro.'
        Require ($commandLine -match 'tail\s+-f\s+/dev/null') 'Keeper command line drifted.'
    }
    return [ordered]@{
        host_pid=$pid
        process_name=[string]$process.Name
        command_line_visible=$commandLineVisible
        command_line=if ($commandLineVisible) { $commandLine } else { $null }
        target_distro_running=$true
    }
}

function Get-TargetHealth {
    try {
        $uri = "http://127.0.0.1:$($script:HttpPort)/?query=SELECT%20version()"
        $version = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri $uri).Content.Trim()
        return [ordered]@{ ready=($version -eq $script:Version); version=$version }
    } catch {
        return [ordered]@{ ready=$false; version=$null }
    }
}

function Get-ServerProcessCount {
    $pattern = '^clickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml'
    $r = Invoke-Runtime "pgrep -af '$pattern'" -AllowFailure
    if ($r.exit_code -eq 1) { return 0 }
    Require ($r.exit_code -eq 0) 'Server process probe failed.'
    return @($r.lines | Where-Object { $_.Trim() }).Count
}
function Get-PidFileState {
    $command = "if [ ! -e '$($script:PidPath)' ] || [ ! -s '$($script:PidPath)' ]; then printf 'EMPTY'; " +
        "elif tr -d '[:space:]' < '$($script:PidPath)' | grep -q .; then printf 'NONEMPTY_DATA'; " +
        "else printf 'WHITESPACE_ONLY'; fi"
    return Runtime-SingleLine $command 'pidfile state'
}

function Assert-PortsFree {
    $ports = @(Get-NetTCPConnection -State Listen -LocalPort $script:HttpPort,$script:NativePort -ErrorAction SilentlyContinue)
    Require ($ports.Count -eq 0) 'Accepted target ports are already listening.'
}

function Validate-CurrentBoundary {
    Require (Test-Path -LiteralPath $script:OriginalPlanPath -PathType Leaf) 'Original frozen plan missing.'
    Require ((Get-Sha $script:OriginalPlanPath) -eq $script:OriginalPlanSha) 'Original frozen plan SHA drifted.'
    Require (Test-Path -LiteralPath $script:PriorJournalPath -PathType Leaf) 'Prior recovery journal missing.'

    $keeper = Get-KeeperFact
    $hot = Get-MountFact $script:HotMount
    $warm = Get-MountFact $script:WarmMount
    Validate-Mount $hot $script:HotUuid $script:HotBytes 'hot_us'
    Validate-Mount $warm $script:WarmUuid $script:WarmBytes 'warm_cn'

    $configSha = Runtime-SingleLine "sha256sum '$($script:ConfigPath)' | cut -d' ' -f1" 'config sha'
    $usersSha = Runtime-SingleLine "sha256sum '$($script:UsersPath)' | cut -d' ' -f1" 'users sha'
    Require ($configSha -eq $script:ConfigSha) 'Accepted target config SHA drifted.'
    Require ($usersSha -eq $script:UsersSha) 'Accepted target users SHA drifted.'
    Require ((Get-ServerProcessCount) -eq 0) 'Accepted target server already exists.'
    Require (-not (Get-TargetHealth).ready) 'Accepted target is already healthy.'
    Assert-PortsFree
    $pidState = Get-PidFileState
    Require ($pidState -in @('EMPTY','WHITESPACE_ONLY')) 'Expected failed server attempt to leave only an empty/whitespace pidfile.'

    return [ordered]@{
        keeper=$keeper; hot_us=$hot; warm_cn=$warm;
        config_sha256=$configSha; users_sha256=$usersSha;
        prior_journal_sha256=(Get-Sha $script:PriorJournalPath);
        pidfile_state=$pidState; server_process_count=0; ports_free=$true
    }
}
function Start-TargetServer {
    $command = "set -eu; rm -f '$($script:PidPath)'; clickhouse server --config-file='$($script:ConfigPath)' --daemon --pid-file='$($script:PidPath)'"
    $r = Invoke-Runtime $command
    Require ($r.exit_code -eq 0) 'Accepted target daemon launch failed.'
    $pid = Runtime-SingleLine "test -s '$($script:PidPath)' && cat '$($script:PidPath)'" 'accepted target server pid'
    Require ($pid -match '^[0-9]+$') 'Accepted target server pid is invalid.'
    $alive = Invoke-Runtime "kill -0 '$pid'" -AllowFailure
    Require ($alive.exit_code -eq 0) 'Accepted target daemon exited immediately.'
    return [int]$pid
}

function Query-Target([string]$Sql) {
    $escaped = [uri]::EscapeDataString($Sql)
    $uri = "http://127.0.0.1:$($script:HttpPort)/?query=$escaped"
    return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri $uri).Content.Trim()
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
    $placement = Query-Target "SELECT disk_name,count(),sum(rows),sum(bytes_on_disk) FROM system.parts WHERE active AND database='markorbit_facts' GROUP BY disk_name ORDER BY disk_name FORMAT TSV"
    $unexpected = Query-Target "SELECT count() FROM system.parts WHERE active AND database='markorbit_facts' AND disk_name NOT IN ('hot_us','warm_cn')"
    Require ([int64]$unexpected -eq 0) 'Unexpected accepted-target active-part placement.'
    return [ordered]@{ health=$health; disks=$disks; policies=$policies; placement=$placement; unexpected_parts=0 }
}
if ($ContractOnly) {
    Write-Host 'ACCEPTED_TARGET_RUNTIME_RESUME_CONTRACT_OK'
    exit 0
}

Assert-ExactMain
$boundary = Validate-CurrentBoundary

if (-not $Apply) {
    $reviewDir = Join-Path $EvidenceRoot 'resume-review'
    New-Item -ItemType Directory -Force -Path $reviewDir | Out-Null
    $plan = [ordered]@{
        version=1; issue=$script:Issue; operation_id=$script:Operation;
        engine_sha=$ExpectedMainSha.ToLowerInvariant();
        state='MOUNTS_ACCEPTED_SERVER_ABSENT';
        original_plan_sha256=$script:OriginalPlanSha;
        prior_journal_sha256=$boundary.prior_journal_sha256;
        keeper=$boundary.keeper; hot_us=$boundary.hot_us; warm_cn=$boundary.warm_cn;
        config_sha256=$boundary.config_sha256; users_sha256=$boundary.users_sha256;
        pidfile_state=$boundary.pidfile_state;
        apply=[ordered]@{
            start_existing_clickhouse=$true; replace_empty_pidfile=$true;
            mount=$false; unmount=$false; format=$false; resize=$false;
            ddl=$false; insert=$false; replay=$false; shutdown=$false; unregister=$false
        }
    }
    $target = if ($PlanPath) { $PlanPath } else { Join-Path $reviewDir 'accepted_target_runtime_resume_plan.json' }
    Write-Json $plan $target
    $sha = Get-Sha $target
    Write-Host "plan_path=$target"
    Write-Host "plan_sha256=$sha"
    Write-Host 'state=MOUNTS_ACCEPTED_SERVER_ABSENT'
    Write-Host "authority_token=GO #769 TARGET-RUNTIME-RESUME $sha START-AND-VERIFY"
    Write-Host 'mutation_performed=False'
    exit 0
}
Require (-not [string]::IsNullOrWhiteSpace($PlanPath)) 'Resume Apply requires -PlanPath.'
Require (Test-Path -LiteralPath $PlanPath -PathType Leaf) 'Frozen resume plan missing.'
$planSha = Get-Sha $PlanPath
$expectedToken = "GO #769 TARGET-RUNTIME-RESUME $planSha START-AND-VERIFY"
Require ($AuthorityToken -ceq $expectedToken) 'Authority token does not match frozen resume plan.'
$plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
Require ([string]$plan.engine_sha -eq $ExpectedMainSha.ToLowerInvariant()) 'Resume plan engine SHA drifted.'
Require ([string]$plan.state -eq 'MOUNTS_ACCEPTED_SERVER_ABSENT') 'Resume plan state drifted.'
Require ([string]$plan.original_plan_sha256 -eq $script:OriginalPlanSha) 'Original plan binding drifted.'
Require ([string]$plan.prior_journal_sha256 -eq $boundary.prior_journal_sha256) 'Prior journal binding drifted.'
Require ([string]$plan.hot_us.uuid -eq $script:HotUuid -and [long]$plan.hot_us.virtual_bytes -eq $script:HotBytes) 'hot_us resume binding drifted.'
Require ([string]$plan.warm_cn.uuid -eq $script:WarmUuid -and [long]$plan.warm_cn.virtual_bytes -eq $script:WarmBytes) 'warm_cn resume binding drifted.'

$applyDir = Join-Path $EvidenceRoot 'resume-apply'
New-Item -ItemType Directory -Force -Path $applyDir | Out-Null
$resumeJournal = Join-Path $applyDir 'accepted_target_runtime_resume_journal.json'
$journal = [ordered]@{
    version=1; plan_sha256=$planSha; engine_sha=$ExpectedMainSha.ToLowerInvariant();
    server_started=$false; validated=$false; server_pid=$null; last_error=$null
}
Write-Json $journal $resumeJournal

try {
    $serverPid = Start-TargetServer
    $journal.server_started=$true
    $journal.server_pid=$serverPid
    Write-Json $journal $resumeJournal

    $ready=$false
    for($i=0; $i -lt 90; $i++) {
        if ((Get-TargetHealth).ready) { $ready=$true; break }
        Start-Sleep -Milliseconds 500
    }
    Require $ready 'Accepted target did not become healthy after daemon resume.'
    $validation = Validate-Target
    $journal.validated=$true
    Write-Json $journal $resumeJournal
    $receiptPath = Join-Path $applyDir 'accepted_target_runtime_recovery_receipt.json'
    $receipt = [ordered]@{
        decision='ACCEPTED_TARGET_RUNTIME_RECOVERED';
        issue=$script:Issue; operation_id=$script:Operation;
        engine_sha=$ExpectedMainSha.ToLowerInvariant(); plan_sha256=$planSha;
        original_plan_sha256=$script:OriginalPlanSha;
        prior_journal_sha256=$boundary.prior_journal_sha256;
        keeper=$boundary.keeper; hot_us=$boundary.hot_us; warm_cn=$boundary.warm_cn;
        server_pid=$serverPid; target=$validation;
        mutation=[ordered]@{
            server_start=$true; empty_pidfile_replaced=$true;
            mount=$false; unmount=$false; format=$false; resize=$false;
            ddl=$false; insert=$false; replay=$false; shutdown=$false; unregister=$false
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
    Write-Json $journal $resumeJournal
    Write-Host "journal_path=$resumeJournal"
    Write-Host 'automatic_unmount_or_rollback_performed=False'
    throw
}
