[CmdletBinding()]
param(
    [string]$ExpectedMainSha,
    [string]$ApplyPlanPath,
    [string]$ExpectedApplyPlanSha256,
    [string]$AuthorityToken,
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $PSScriptRoot
$script:Issue = 823
$script:PlanVersion = 'HOT_GLOBAL_FOUNDATION_APPLY_PLAN_V1'
$script:ReceiptVersion = 'HOT_GLOBAL_FOUNDATION_APPLY_RECEIPT_V1'
$script:TargetDistro = 'MarkOrbit-ClickHouse'
$script:TargetHost = '127.0.0.1'
$script:TargetPort = '29000'
$script:TargetConfig = '/opt/markorbit-clickhouse-production/config.xml'
$script:VhdxPath = 'E:\MarkOrbitData\production\clickhouse\hot_global.vhdx'
$script:VhdxMaxBytes = [int64]17179869184
$script:VhdxMaxMiB = [int64]16384
$script:Ext4Label = 'mo_hot_global_prod'
$script:MountName = 'markorbit_prod_hot_global'
$script:MountPath = '/mnt/wsl/markorbit_prod_hot_global'
$script:DiskPath = '/mnt/wsl/markorbit_prod_hot_global/clickhouse-data/'
$script:DiskName = 'hot_global'
$script:PolicyName = 'hot_global_only'
$script:ExpectedHotUsPath = '/mnt/wsl/markorbit_prod_hot_us/clickhouse-data/'
$script:ExpectedWarmCnPath = '/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/'

function Invoke-NativeText {
    param(
        [Parameter(Mandatory=$true)][string]$Command,
        [Parameter(Mandatory=$true)][AllowEmptyCollection()][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $previous=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        $lines=@(& $Command @Arguments 2>&1)
        $code=$LASTEXITCODE
    }
    finally { $ErrorActionPreference=$previous }
    $text=@($lines|ForEach-Object{$_.ToString()})
    if(-not $AllowFailure -and $code -ne 0){throw "$Command failed with exit code \${code}: $($text -join [Environment]::NewLine)"}
    return [ordered]@{exit_code=$code;lines=@($text)}
}

function Write-JsonFile([object]$Value,[string]$Path) {
    $parent=Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent|Out-Null
    $json=$Value|ConvertTo-Json -Depth 16
    [IO.File]::WriteAllText($Path,$json,(New-Object Text.UTF8Encoding($false)))
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Assert-ExactMain([string]$Expected) {
    if($Expected -notmatch '^[0-9a-fA-F]{40}$'){throw 'ExpectedMainSha must be exact 40-character SHA.'}
    $head=(git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin=(git rev-parse origin/main).Trim().ToLowerInvariant()
    $e=$Expected.Trim().ToLowerInvariant()
    if($head -ne $e -or $origin -ne $e){throw "Exact main drift. expected=$e head=$head origin=$origin"}
    if(git status --porcelain=v1){throw 'Working tree must be clean.'}
    return $head
}

function Get-RunningWslNames {
    $p=Invoke-NativeText 'wsl.exe' @('--list','--running','--quiet')
    return @($p.lines|ForEach-Object{([string]$_).Replace([string][char]0,'').Trim()}|Where-Object{$_})
}

function Assert-TargetRunning {
    if(@(Get-RunningWslNames) -notcontains $script:TargetDistro){throw "Target distro is not running: $($script:TargetDistro)"}
    $p=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','pgrep','-f','[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml')
    $pids=@($p.lines|Where-Object{$_.Trim() -match '^\d+$'})
    if($pids.Count -ne 1){throw "Expected exact-one target ClickHouse process; observed=$($pids.Count)"}
}

function Invoke-TargetRows([string]$Sql,[string]$Label) {
    Assert-TargetRunning
    $p=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,'--query',($Sql+' FORMAT JSONEachRow'))
    $rows=@()
    foreach($line in $p.lines){
        $t=([string]$line).Trim()
        if(-not $t){continue}
        try{$rows+=($t|ConvertFrom-Json)}catch{throw "$Label returned invalid JSON: $t"}
    }
    return @($rows)
}

function Invoke-TargetSql([string]$Sql,[switch]$AllowFailure) {
    Assert-TargetRunning
    return Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,'--query',$Sql) -AllowFailure:$AllowFailure
}

function Get-EState {
    $v=Get-Volume -DriveLetter E -ErrorAction Stop
    [int64]$recommended=[math]::Ceiling([double]$v.Size*0.30)
    [int64]$hard=[math]::Ceiling([double]$v.Size*0.20)
    $state=if($v.SizeRemaining -lt $hard){'BLOCKED_BELOW_HARD_RESERVE'}elseif($v.SizeRemaining -lt $recommended){'BELOW_RECOMMENDED_RESERVE'}else{'READY'}
    return [ordered]@{total_bytes=[int64]$v.Size;free_bytes=[int64]$v.SizeRemaining;recommended_30pct_floor_bytes=$recommended;hard_20pct_floor_bytes=$hard;state=$state}
}

function Get-TargetFileSha([string]$Path) {
    $p=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','sha256sum',$Path)
    return (((@($p.lines)-join '').Trim() -split '\s+')[0]).ToLowerInvariant()
}

function Convert-WindowsPathToWsl([string]$Path) {
    $full=[IO.Path]::GetFullPath($Path)
    if($full -notmatch '^([A-Za-z]):\\(.*)$'){throw "Cannot map Windows path to WSL: $full"}
    $drive=$Matches[1].ToLowerInvariant()
    $tail=$Matches[2] -replace '\\','/'
    return "/mnt/$drive/$tail"
}

function Get-WslBlockDisks([string]$Distro) {
    $p=Invoke-NativeText 'wsl.exe' @('-d',$Distro,'-u','root','--','lsblk','-dn','-o','NAME,TYPE') -AllowFailure
    if($p.exit_code -ne 0){throw "Unable to inventory block disks in $Distro"}
    $names=@()
    foreach($line in $p.lines){
        $f=@($line.Trim() -split '\s+')
        if($f.Count -ge 2 -and $f[1] -eq 'disk'){$names+=$f[0]}
    }
    return @($names)
}

function Get-MountFact([string]$Distro) {
    $p=Invoke-NativeText 'wsl.exe' @('-d',$Distro,'-u','root','--','findmnt','-n','-T',$script:MountPath,'-o','FSTYPE,SOURCE,TARGET') -AllowFailure
    $text=(@($p.lines)-join ' ').Trim()
    $parts=@($text -split '\s+')
    return [ordered]@{
        ready=[bool]($p.exit_code -eq 0 -and $parts.Count -ge 3 -and $parts[0] -eq 'ext4')
        exit_code=$p.exit_code
        fstype=if($parts.Count -ge 1){$parts[0]}else{$null}
        source=if($parts.Count -ge 2){$parts[1]}else{$null}
        target=if($parts.Count -ge 3){$parts[2]}else{$null}
    }
}

function Read-ApplyPlan([string]$Path,[string]$ExpectedSha,[string]$ExpectedMain) {
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)){throw "Apply plan missing: $Path"}
    if($ExpectedSha -notmatch '^[0-9a-fA-F]{64}$'){throw 'ExpectedApplyPlanSha256 must be SHA-256-shaped.'}
    $actual=Get-FileSha256 $Path
    if($actual -ne $ExpectedSha.Trim().ToLowerInvariant()){throw "Apply plan SHA drift. expected=$ExpectedSha actual=$actual"}
    $p=Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if([string]$p.version -ne $script:PlanVersion){throw 'Apply plan version drifted.'}
    if([int]$p.issue -ne $script:Issue){throw 'Apply plan issue drifted.'}
    if([string]$p.main_sha -ne $ExpectedMain.Trim().ToLowerInvariant()){throw 'Apply plan main SHA drifted.'}
    if([bool]$p.mutation_authorized){throw 'Apply plan unexpectedly self-authorizes mutation.'}
    if([string]$p.target.vhdx_path -ne $script:VhdxPath){throw 'Apply plan VHDX path drifted.'}
    if([int64]$p.target.max_bytes -ne $script:VhdxMaxBytes){throw 'Apply plan max bytes drifted.'}
    if([int64]$p.target.maximum_mib -ne $script:VhdxMaxMiB){throw 'Apply plan maximum MiB drifted.'}
    if([string]$p.target.mount_name -ne $script:MountName){throw 'Apply plan mount name drifted.'}
    if([string]$p.target.clickhouse_disk_path -ne $script:DiskPath){throw 'Apply plan disk path drifted.'}
    if([string]$p.target.clickhouse_disk -ne $script:DiskName){throw 'Apply plan disk name drifted.'}
    if([string]$p.target.clickhouse_policy -ne $script:PolicyName){throw 'Apply plan policy drifted.'}
    if(-not (Test-Path -LiteralPath ([string]$p.config.proposed_path) -PathType Leaf)){throw 'Proposed config evidence file is missing.'}
    if((Get-FileSha256 ([string]$p.config.proposed_path)) -ne [string]$p.config.proposed_sha256){throw 'Proposed config evidence SHA drifted.'}
    if(-not (Test-Path -LiteralPath ([string]$p.capacity_plan.path) -PathType Leaf)){throw 'Capacity plan file is missing.'}
    if((Get-FileSha256 ([string]$p.capacity_plan.path)) -ne [string]$p.capacity_plan.sha256){throw 'Capacity plan SHA drifted after PREPARE.'}
    return [ordered]@{path=[IO.Path]::GetFullPath($Path);sha256=$actual;value=$p}
}

function Get-StorageIdentity {
    $disks=@(Invoke-TargetRows "SELECT name,path,total_space,free_space FROM system.disks WHERE name IN ('hot_us','warm_cn','hot_global') ORDER BY name" 'target disks')
    $policies=@(Invoke-TargetRows "SELECT policy_name,volume_name,volume_priority,disks FROM system.storage_policies WHERE policy_name IN ('hot_us_only','warm_cn_only','hot_global_only') ORDER BY policy_name,volume_priority" 'target policies')
    return [ordered]@{disks=@($disks);policies=@($policies)}
}

function Assert-AcceptedBaseline([object]$Identity) {
    $hotUs=@($Identity.disks|Where-Object{[string]$_.name -eq 'hot_us'})
    $warm=@($Identity.disks|Where-Object{[string]$_.name -eq 'warm_cn'})
    if($hotUs.Count -ne 1 -or [string]$hotUs[0].path -ne $script:ExpectedHotUsPath){throw 'Accepted hot_us disk baseline drifted.'}
    if($warm.Count -ne 1 -or [string]$warm[0].path -ne $script:ExpectedWarmCnPath){throw 'Accepted warm_cn disk baseline drifted.'}
    $hotUsP=@($Identity.policies|Where-Object{[string]$_.policy_name -eq 'hot_us_only'})
    $warmP=@($Identity.policies|Where-Object{[string]$_.policy_name -eq 'warm_cn_only'})
    if($hotUsP.Count -ne 1 -or @($hotUsP[0].disks).Count -ne 1 -or [string]$hotUsP[0].disks[0] -ne 'hot_us'){throw 'Accepted hot_us_only policy drifted.'}
    if($warmP.Count -ne 1 -or @($warmP[0].disks).Count -ne 1 -or [string]$warmP[0].disks[0] -ne 'warm_cn'){throw 'Accepted warm_cn_only policy drifted.'}
}

function Get-FoundationState([object]$Plan) {
    $vhdx=[bool](Test-Path -LiteralPath $script:VhdxPath -PathType Leaf)
    $mount=Get-MountFact ([string]$Plan.target.tooling_distro)
    $identity=Get-StorageIdentity
    Assert-AcceptedBaseline $identity
    $disk=@($identity.disks|Where-Object{[string]$_.name -eq 'hot_global'})
    $policy=@($identity.policies|Where-Object{[string]$_.policy_name -eq 'hot_global_only'})
    $configSha=Get-TargetFileSha $script:TargetConfig
    $parts=@(Invoke-TargetRows "SELECT count() AS n FROM system.parts WHERE disk_name='hot_global' AND active" 'hot_global active parts')
    [int64]$partCount=if($parts.Count -eq 1){[int64]$parts[0].n}else{[int64]-1}
    $accepted=(
        $vhdx -and
        [bool]$mount.ready -and
        $disk.Count -eq 1 -and [string]$disk[0].path -eq $script:DiskPath -and
        $policy.Count -eq 1 -and @($policy[0].disks).Count -eq 1 -and [string]$policy[0].disks[0] -eq 'hot_global' -and
        $configSha -eq [string]$Plan.config.proposed_sha256 -and
        $partCount -eq 0
    )
    $absent=(
        -not $vhdx -and
        -not [bool]$mount.ready -and
        $disk.Count -eq 0 -and
        $policy.Count -eq 0 -and
        $configSha -eq [string]$Plan.config.before_sha256
    )
    $state=if($accepted){'ACCEPTED'}elseif($absent){'ABSENT'}else{'PARTIAL'}
    return [ordered]@{
        state=$state
        vhdx_exists=$vhdx
        mount=$mount
        identity=$identity
        hot_global_disk_count=$disk.Count
        hot_global_policy_count=$policy.Count
        active_part_count=$partCount
        config_sha256=$configSha
    }
}

function Save-Journal([object]$Journal,[string]$Path) {
    $Journal.updated_at=[DateTimeOffset]::UtcNow.ToString('o')
    Write-JsonFile $Journal $Path
}

function Assert-ToolingReady([string]$Distro) {
    if(-not (Get-Command diskpart.exe -ErrorAction SilentlyContinue)){throw 'diskpart.exe is not available.'}
    $p=Invoke-NativeText 'wsl.exe' @('-d',$Distro,'-u','root','--','sh','-lc','for c in mkfs.ext4 lsblk blkid findmnt sha256sum; do command -v "$c" >/dev/null 2>&1 || exit 10; done') -AllowFailure
    if($p.exit_code -ne 0){throw "Tooling distro missing required ext4 tools: $Distro"}
}

if($ContractOnly){
    if($script:VhdxMaxBytes -ne 17179869184 -or $script:VhdxMaxMiB -ne 16384){throw 'hot_global capacity constant drifted.'}
    if($script:VhdxPath -ne 'E:\MarkOrbitData\production\clickhouse\hot_global.vhdx'){throw 'hot_global VHDX path drifted.'}
    if($script:DiskName -ne 'hot_global' -or $script:PolicyName -ne 'hot_global_only'){throw 'hot_global ClickHouse identity drifted.'}
    $sample='abc'
    if(("GO #823 HOT-GLOBAL $sample PROVISION-RELOAD-VERIFY") -ne 'GO #823 HOT-GLOBAL abc PROVISION-RELOAD-VERIFY'){throw 'Authority token contract drifted.'}
    Write-Host 'HOT_GLOBAL_FOUNDATION_APPLY_CONTRACT_PASS'
    exit 0
}

foreach($required in @('ExpectedMainSha','ApplyPlanPath','ExpectedApplyPlanSha256','AuthorityToken')){
    if(-not (Get-Variable -Name $required -ValueOnly)){throw "$required is required."}
}

Push-Location $repoRoot
try {
    $mainSha=Assert-ExactMain $ExpectedMainSha
    $planInfo=Read-ApplyPlan $ApplyPlanPath $ExpectedApplyPlanSha256 $mainSha
    $plan=$planInfo.value
    $expectedToken="GO #823 HOT-GLOBAL $($planInfo.sha256) PROVISION-RELOAD-VERIFY"
    if($AuthorityToken -cne $expectedToken){throw 'AuthorityToken does not exactly match the frozen apply-plan SHA.'}
    Assert-ToolingReady ([string]$plan.target.tooling_distro)
    Assert-TargetRunning
    $eBefore=Get-EState
    if([string]$eBefore.state -ne 'READY'){throw "E reserve is not READY before Apply: $($eBefore.state)"}
    $stateBefore=Get-FoundationState $plan
    if([string]$stateBefore.state -eq 'PARTIAL'){throw 'hot_global is in partial state; refusing automatic mutation.'}

    $stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir=[IO.Path]::GetFullPath((Join-Path $EvidenceRoot "hot_global_foundation_apply_$stamp"))
    $repoFull=[IO.Path]::GetFullPath($repoRoot)
    if($evidenceDir.StartsWith($repoFull,[StringComparison]::OrdinalIgnoreCase)){throw 'EvidenceRoot must be outside the Git worktree.'}
    New-Item -ItemType Directory -Force -Path $evidenceDir|Out-Null
    $journalPath=Join-Path $evidenceDir 'hot_global_foundation_apply_journal.json'
    $journal=[ordered]@{
        version='HOT_GLOBAL_FOUNDATION_APPLY_JOURNAL_V1'
        issue=$script:Issue
        main_sha=$mainSha
        apply_plan_sha256=$planInfo.sha256
        authority_token_sha256=([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($AuthorityToken))).Replace('-','').ToLowerInvariant())
        started_at=[DateTimeOffset]::UtcNow.ToString('o')
        updated_at=[DateTimeOffset]::UtcNow.ToString('o')
        stage='preflight'
        initial_state=[string]$stateBefore.state
        vhdx_created=$false
        ext4_formatted=$false
        ext4_uuid=$null
        named_mount_ready=$false
        directory_ready=$false
        config_replaced=$false
        config_reloaded=$false
        verification_passed=$false
        config_backup_path=$null
    }
    Save-Journal $journal $journalPath

    if([string]$stateBefore.state -eq 'ACCEPTED'){
        $journal.stage='already_accepted'
        $journal.verification_passed=$true
        Save-Journal $journal $journalPath
    }
    else {
        $admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'hot_global Apply requires elevated Administrator PowerShell.'}

        $journal.stage='create_vhdx'
        Save-Journal $journal $journalPath
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $script:VhdxPath)|Out-Null
        $diskpartPath=Join-Path $evidenceDir 'diskpart_create_hot_global.txt'
        @(
            ('create vdisk file="{0}" maximum={1} type=expandable' -f $script:VhdxPath,$script:VhdxMaxMiB),
            'exit'
        ) | Set-Content -LiteralPath $diskpartPath -Encoding ASCII
        $create=Invoke-NativeText 'diskpart.exe' @('/s',$diskpartPath) -AllowFailure
        if($create.exit_code -ne 0 -or -not (Test-Path -LiteralPath $script:VhdxPath -PathType Leaf)){
            throw "hot_global VHDX create failed: $($create.lines -join [Environment]::NewLine)"
        }
        $journal.vhdx_created=$true
        Save-Journal $journal $journalPath

        $journal.stage='format_ext4'
        Save-Journal $journal $journalPath
        $tooling=[string]$plan.target.tooling_distro
        $before=@(Get-WslBlockDisks $tooling)
        $bare=Invoke-NativeText 'wsl.exe' @('--mount','--vhd',$script:VhdxPath,'--bare') -AllowFailure
        if($bare.exit_code -ne 0){throw 'Unable to bare-mount new hot_global VHDX.'}
        Start-Sleep -Seconds 1
        $after=@(Get-WslBlockDisks $tooling)
        $new=@($after|Where-Object{$_ -notin $before})
        if($new.Count -ne 1){throw "Expected exactly one new WSL block disk; observed=$($new -join ',')"}
        $device="/dev/$($new[0])"
        $sizeProbe=Invoke-NativeText 'wsl.exe' @('-d',$tooling,'-u','root','--','lsblk','-b','-dn','-o','SIZE',$device)
        [int64]$deviceBytes=((@($sizeProbe.lines)-join '').Trim())
        if($deviceBytes -ne $script:VhdxMaxBytes){throw "New hot_global block device size mismatch. expected=$($script:VhdxMaxBytes) observed=$deviceBytes"}
        $mkfs=Invoke-NativeText 'wsl.exe' @('-d',$tooling,'-u','root','--','mkfs.ext4','-F','-L',$script:Ext4Label,$device) -AllowFailure
        if($mkfs.exit_code -ne 0){throw "mkfs.ext4 failed: $($mkfs.lines -join [Environment]::NewLine)"}
        $blkid=Invoke-NativeText 'wsl.exe' @('-d',$tooling,'-u','root','--','blkid','-s','UUID','-o','value',$device)
        $uuid=(@($blkid.lines)-join '').Trim()
        if(-not $uuid){throw 'Unable to capture hot_global ext4 UUID.'}
        $unmount=Invoke-NativeText 'wsl.exe' @('--unmount',$script:VhdxPath) -AllowFailure
        if($unmount.exit_code -ne 0){throw 'Exact hot_global VHDX unmount after format failed.'}
        $journal.ext4_formatted=$true
        $journal.ext4_uuid=$uuid
        Save-Journal $journal $journalPath

        $journal.stage='mount_named_ext4'
        Save-Journal $journal $journalPath
        $mount=Invoke-NativeText 'wsl.exe' @('--mount','--vhd',$script:VhdxPath,'--name',$script:MountName) -AllowFailure
        if($mount.exit_code -ne 0){throw "Named hot_global mount failed: $($mount.lines -join [Environment]::NewLine)"}
        Start-Sleep -Seconds 1
        $mountFact=Get-MountFact $tooling
        if(-not [bool]$mountFact.ready -or [string]$mountFact.fstype -ne 'ext4'){throw 'Named hot_global mount is not ext4.'}
        $journal.named_mount_ready=$true
        Save-Journal $journal $journalPath

        $journal.stage='prepare_empty_directory'
        Save-Journal $journal $journalPath
        $prepareCmd="set -eu; mkdir -p '$($script:DiskPath)'; chown root:root '$($script:DiskPath)'; chmod 0770 '$($script:DiskPath)'; if find '$($script:DiskPath)' -mindepth 1 -print -quit | grep -q .; then exit 20; fi"
        $prepare=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','sh','-lc',$prepareCmd) -AllowFailure
        if($prepare.exit_code -ne 0){throw 'hot_global clickhouse-data directory is not freshly empty.'}
        $journal.directory_ready=$true
        Save-Journal $journal $journalPath

        $journal.stage='replace_config_and_reload'
        Save-Journal $journal $journalPath
        $currentConfigSha=Get-TargetFileSha $script:TargetConfig
        if($currentConfigSha -ne [string]$plan.config.before_sha256){throw "Target config SHA drifted before replacement. expected=$($plan.config.before_sha256) actual=$currentConfigSha"}
        $proposedPath=[string]$plan.config.proposed_path
        if((Get-FileSha256 $proposedPath) -ne [string]$plan.config.proposed_sha256){throw 'Proposed config file SHA drifted before Apply.'}
        $backupPath="$($script:TargetConfig).hot-global-backup-$($planInfo.sha256.Substring(0,12))"
        $journal.config_backup_path=$backupPath
        $proposedBytes=[IO.File]::ReadAllBytes($proposedPath)
        $base64=[Convert]::ToBase64String($proposedBytes)
        $pending="$($script:TargetConfig).hot-global-pending"
        $copyCmd="set -eu; test ! -e '$backupPath'; cp '$($script:TargetConfig)' '$backupPath'; echo '$base64' | base64 -d > '$pending'; chmod 0644 '$pending'; mv '$pending' '$($script:TargetConfig)'"
        $copy=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','sh','-lc',$copyCmd) -AllowFailure
        if($copy.exit_code -ne 0){throw "Unable to atomically replace target config: $($copy.lines -join [Environment]::NewLine)"}
        $journal.config_replaced=$true
        Save-Journal $journal $journalPath

        try {
            $afterConfigSha=Get-TargetFileSha $script:TargetConfig
            if($afterConfigSha -ne [string]$plan.config.proposed_sha256){throw 'Target config SHA does not match frozen proposed config.'}
            $reload=Invoke-TargetSql 'SYSTEM RELOAD CONFIG' -AllowFailure
            if($reload.exit_code -ne 0){throw "SYSTEM RELOAD CONFIG failed: $($reload.lines -join [Environment]::NewLine)"}
            $journal.config_reloaded=$true
            Save-Journal $journal $journalPath

            $ready=$false
            for($i=0;$i -lt 15;$i++){
                Start-Sleep -Seconds 1
                try {
                    $probeIdentity=Get-StorageIdentity
                    $d=@($probeIdentity.disks|Where-Object{[string]$_.name -eq 'hot_global'})
                    $p=@($probeIdentity.policies|Where-Object{[string]$_.policy_name -eq 'hot_global_only'})
                    if($d.Count -eq 1 -and [string]$d[0].path -eq $script:DiskPath -and $p.Count -eq 1 -and @($p[0].disks).Count -eq 1 -and [string]$p[0].disks[0] -eq 'hot_global'){
                        $ready=$true
                        break
                    }
                } catch {}
            }
            if(-not $ready){throw 'hot_global disk/policy did not appear after config reload.'}
        }
        catch {
            $journal['config_rollback_attempted']=$true
            Save-Journal $journal $journalPath
            $restoreCmd="set -eu; test -f '$backupPath'; cp '$backupPath' '$($script:TargetConfig)'"
            $restore=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','sh','-lc',$restoreCmd) -AllowFailure
            $reloadBack=Invoke-TargetSql 'SYSTEM RELOAD CONFIG' -AllowFailure
            $journal['config_rollback_succeeded']=[bool]($restore.exit_code -eq 0 -and $reloadBack.exit_code -eq 0)
            Save-Journal $journal $journalPath
            throw
        }

        $journal.stage='verify'
        Save-Journal $journal $journalPath
        $final=Get-FoundationState $plan
        if([string]$final.state -ne 'ACCEPTED'){throw "Final hot_global foundation state is not ACCEPTED: $($final.state)"}
        $eAfter=Get-EState
        if([string]$eAfter.state -ne 'READY'){throw "E reserve is not READY after Apply: $($eAfter.state)"}
        Assert-ExactMain $mainSha | Out-Null
        $journal.verification_passed=$true
        $journal.stage='complete'
        Save-Journal $journal $journalPath
    }

    $stateAfter=Get-FoundationState $plan
    if([string]$stateAfter.state -ne 'ACCEPTED'){throw 'hot_global foundation acceptance verification failed.'}
    $eAfterFinal=Get-EState
    if([string]$eAfterFinal.state -ne 'READY'){throw 'E reserve is not READY at final receipt.'}
    Assert-ExactMain $mainSha | Out-Null

    $receipt=[ordered]@{
        version=$script:ReceiptVersion
        status='PASS'
        issue=$script:Issue
        completed_at=[DateTimeOffset]::UtcNow.ToString('o')
        main_sha=$mainSha
        apply_plan=[ordered]@{path=$planInfo.path;sha256=$planInfo.sha256;version=$script:PlanVersion}
        capacity_plan=[ordered]@{path=[string]$plan.capacity_plan.path;sha256=[string]$plan.capacity_plan.sha256}
        authority=[ordered]@{token_sha256=[string]$journal.authority_token_sha256}
        result=if([string]$stateBefore.state -eq 'ACCEPTED'){'ALREADY_ACCEPTED_NOOP'}else{'HOT_GLOBAL_FOUNDATION_PROVISIONED'}
        topology=[ordered]@{
            vhdx_path=$script:VhdxPath
            max_bytes=$script:VhdxMaxBytes
            ext4_label=$script:Ext4Label
            ext4_uuid=[string]$journal.ext4_uuid
            mount_name=$script:MountName
            mount_path=$script:MountPath
            disk_path=$script:DiskPath
            disk_name=$script:DiskName
            storage_policy=$script:PolicyName
        }
        config=[ordered]@{
            runtime_path=$script:TargetConfig
            final_sha256=[string]$stateAfter.config_sha256
            proposed_sha256=[string]$plan.config.proposed_sha256
            hot_reload_used=[bool]$journal.config_reloaded
            target_restart_performed=$false
        }
        storage=[ordered]@{
            foundation_state=[string]$stateAfter.state
            active_part_count=[int64]$stateAfter.active_part_count
            mount=$stateAfter.mount
            e_before=$eBefore
            e_after=$eAfterFinal
        }
        mutation=[ordered]@{
            vhdx_create_performed=[bool]$journal.vhdx_created
            ext4_format_performed=[bool]$journal.ext4_formatted
            named_mount_performed=[bool]$journal.named_mount_ready
            config_replace_performed=[bool]$journal.config_replaced
            config_reload_performed=[bool]$journal.config_reloaded
            clickhouse_restart_performed=$false
            hot_us_or_warm_cn_mutation_performed=$false
            table_schema_or_data_mutation_performed=$false
            data_copy_or_replay_performed=$false
            docker_mutation_performed=$false
            serving_cutover_performed=$false
        }
        journal_path=$journalPath
    }
    $receiptPath=Join-Path $evidenceDir 'hot_global_foundation_apply_receipt.json'
    Write-JsonFile $receipt $receiptPath
    $receiptSha=Get-FileSha256 $receiptPath

    Write-Host 'HOT_GLOBAL_FOUNDATION_APPLY_PASS'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "receipt_sha256=$receiptSha"
    Write-Host "result=$($receipt.result)"
    Write-Host "foundation_state=$($stateAfter.state)"
    Write-Host "active_part_count=$($stateAfter.active_part_count)"
    Write-Host "e_reserve_state=$($eAfterFinal.state)"
}
finally { Pop-Location }
