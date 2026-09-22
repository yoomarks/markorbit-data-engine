[CmdletBinding()]
param(
    [string]$ExpectedMainSha,
    [string]$CapacityPlanPath,
    [string]$ExpectedCapacityPlanSha256,
    [string]$EvidenceRoot = 'reports',
    [string]$ToolingDistro = 'Ubuntu-24.04',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $PSScriptRoot
$script:Issue = 823
$script:PlanVersion = 'HOT_GLOBAL_FOUNDATION_APPLY_PLAN_V1'
$script:CapacityPlanVersion = 'HOT_GLOBAL_INITIAL_CAPACITY_PLAN_V1'
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
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& $Command @Arguments 2>&1)
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $text = @($lines | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $code -ne 0) {
        throw "$Command failed with exit code ${code}: $($text -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$code; lines=@($text) }
}

function Write-JsonFile([object]$Value,[string]$Path) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $json = $Value | ConvertTo-Json -Depth 14
    [IO.File]::WriteAllText($Path,$json,(New-Object Text.UTF8Encoding($false)))
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Assert-ExactMain([string]$Expected) {
    if ($Expected -notmatch '^[0-9a-fA-F]{40}$') { throw 'ExpectedMainSha must be an exact 40-character SHA.' }
    $head=(git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin=(git rev-parse origin/main).Trim().ToLowerInvariant()
    $expectedLower=$Expected.Trim().ToLowerInvariant()
    if ($head -ne $expectedLower -or $origin -ne $expectedLower) {
        throw "Exact main drift. expected=$expectedLower head=$head origin=$origin"
    }
    if (git status --porcelain=v1) { throw 'Working tree must be clean.' }
    return $head
}

function Get-RunningWslNames {
    $probe=Invoke-NativeText 'wsl.exe' @('--list','--running','--quiet')
    return @($probe.lines | ForEach-Object { ([string]$_).Replace([string][char]0,'').Trim() } | Where-Object { $_ })
}

function Assert-TargetRunning {
    $running=@(Get-RunningWslNames)
    if ($running -notcontains $script:TargetDistro) {
        throw "Accepted target distro is not already running: $($script:TargetDistro)"
    }
    $p=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','pgrep','-f','[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml')
    $pids=@($p.lines | Where-Object { $_.Trim() -match '^\d+$' })
    if ($pids.Count -ne 1) { throw "Expected exact-one accepted target ClickHouse process; observed=$($pids.Count)" }
}

function Invoke-TargetRows([string]$Sql,[string]$Label) {
    Assert-TargetRunning
    $probe=Invoke-NativeText 'wsl.exe' @(
        '-d',$script:TargetDistro,'-u','root','--',
        'clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,
        '--query',($Sql + ' FORMAT JSONEachRow')
    )
    $rows=@()
    foreach($line in $probe.lines) {
        $t=([string]$line).Trim()
        if(-not $t){continue}
        try { $rows += ($t | ConvertFrom-Json) }
        catch { throw "$Label returned invalid JSONEachRow: $t" }
    }
    return @($rows)
}

function Get-EState {
    $v=Get-Volume -DriveLetter E -ErrorAction Stop
    [int64]$recommended=[math]::Ceiling([double]$v.Size*0.30)
    [int64]$hard=[math]::Ceiling([double]$v.Size*0.20)
    $state=if($v.SizeRemaining -lt $hard){'BLOCKED_BELOW_HARD_RESERVE'}elseif($v.SizeRemaining -lt $recommended){'BELOW_RECOMMENDED_RESERVE'}else{'READY'}
    return [ordered]@{
        total_bytes=[int64]$v.Size
        free_bytes=[int64]$v.SizeRemaining
        recommended_30pct_floor_bytes=$recommended
        hard_20pct_floor_bytes=$hard
        state=$state
    }
}

function Assert-ToolingReady {
    if(-not (Get-Command diskpart.exe -ErrorAction SilentlyContinue)){throw 'diskpart.exe is not available.'}
    $help=(Invoke-NativeText 'wsl.exe' @('--help')).lines -join [Environment]::NewLine
    if($help -notmatch '--vhd'){throw 'WSL --vhd support is not available.'}
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','sh','-lc','for c in mkfs.ext4 lsblk blkid findmnt sha256sum; do command -v "$c" >/dev/null 2>&1 || exit 10; done') -AllowFailure
    if($probe.exit_code -ne 0){throw "Tooling distro is missing required ext4 tools: $ToolingDistro"}
}

function Get-TargetConfigText {
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','cat',$script:TargetConfig)
    return (@($probe.lines) -join [Environment]::NewLine) + [Environment]::NewLine
}

function Get-TargetFileSha([string]$Path) {
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','sha256sum',$Path)
    $text=(@($probe.lines)-join '').Trim()
    return ($text -split '\s+')[0].ToLowerInvariant()
}

function Assert-CapacityPlan([string]$Path,[string]$ExpectedSha) {
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)){throw "Capacity plan missing: $Path"}
    if($ExpectedSha -notmatch '^[0-9a-fA-F]{64}$'){throw 'ExpectedCapacityPlanSha256 must be SHA-256-shaped.'}
    $actual=Get-FileSha256 $Path
    if($actual -ne $ExpectedSha.Trim().ToLowerInvariant()){throw "Capacity plan SHA drift. expected=$ExpectedSha actual=$actual"}
    $p=Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if([string]$p.plan_version -ne $script:CapacityPlanVersion){throw 'Capacity plan version drifted.'}
    if([string]$p.target.vhdx_path -ne $script:VhdxPath){throw 'Capacity plan VHDX path drifted.'}
    if([int64]$p.target.max_bytes -ne $script:VhdxMaxBytes){throw 'Capacity plan VHDX max bytes drifted.'}
    if([string]$p.target.mount_name -ne $script:MountName){throw 'Capacity plan mount name drifted.'}
    if([string]$p.target.clickhouse_disk_path -ne $script:DiskPath){throw 'Capacity plan disk path drifted.'}
    if([string]$p.target.clickhouse_disk -ne $script:DiskName){throw 'Capacity plan disk name drifted.'}
    if([string]$p.target.clickhouse_policy -ne $script:PolicyName){throw 'Capacity plan policy name drifted.'}
    if([string]$p.target.filesystem -ne 'ext4'){throw 'Capacity plan filesystem drifted.'}
    if([bool]$p.production_mutation_authorized){throw 'Capacity plan unexpectedly authorizes mutation.'}
    return [ordered]@{ path=[IO.Path]::GetFullPath($Path); sha256=$actual; value=$p }
}

function Assert-TargetBaseline {
    $disks=@(Invoke-TargetRows "SELECT name,path,total_space,free_space FROM system.disks WHERE name IN ('hot_us','warm_cn','hot_global') ORDER BY name" 'target disks')
    $hotUs=@($disks|Where-Object{[string]$_.name -eq 'hot_us'})
    $warm=@($disks|Where-Object{[string]$_.name -eq 'warm_cn'})
    $global=@($disks|Where-Object{[string]$_.name -eq 'hot_global'})
    if($hotUs.Count -ne 1 -or [string]$hotUs[0].path -ne $script:ExpectedHotUsPath){throw 'Accepted hot_us disk baseline drifted.'}
    if($warm.Count -ne 1 -or [string]$warm[0].path -ne $script:ExpectedWarmCnPath){throw 'Accepted warm_cn disk baseline drifted.'}
    if($global.Count -ne 0){throw 'hot_global disk already exists.'}

    $policies=@(Invoke-TargetRows "SELECT policy_name,volume_name,volume_priority,disks FROM system.storage_policies WHERE policy_name IN ('hot_us_only','warm_cn_only','hot_global_only') ORDER BY policy_name,volume_priority" 'target policies')
    $hotUsP=@($policies|Where-Object{[string]$_.policy_name -eq 'hot_us_only'})
    $warmP=@($policies|Where-Object{[string]$_.policy_name -eq 'warm_cn_only'})
    $globalP=@($policies|Where-Object{[string]$_.policy_name -eq 'hot_global_only'})
    if($hotUsP.Count -ne 1 -or @($hotUsP[0].disks).Count -ne 1 -or [string]$hotUsP[0].disks[0] -ne 'hot_us'){throw 'Accepted hot_us_only policy drifted.'}
    if($warmP.Count -ne 1 -or @($warmP[0].disks).Count -ne 1 -or [string]$warmP[0].disks[0] -ne 'warm_cn'){throw 'Accepted warm_cn_only policy drifted.'}
    if($globalP.Count -ne 0){throw 'hot_global_only policy already exists.'}
    return [ordered]@{ disks=@($disks); policies=@($policies) }
}

function New-ProposedConfig([string]$CurrentText,[string]$OutputPath) {
    [xml]$xml=$CurrentText
    $root=$xml.clickhouse
    if($null -eq $root){throw 'Target config lacks <clickhouse> root.'}
    $storage=$root.storage_configuration
    if($null -eq $storage){throw 'Target config lacks storage_configuration.'}
    $disks=$storage.disks
    $policies=$storage.policies
    if($null -eq $disks -or $null -eq $policies){throw 'Target config lacks disks or policies.'}
    if($null -ne $disks.hot_global -or $null -ne $policies.hot_global_only){throw 'Target config already contains hot_global identity.'}

    $diskNode=$xml.CreateElement('hot_global')
    $typeNode=$xml.CreateElement('type'); $typeNode.InnerText='local'
    $pathNode=$xml.CreateElement('path'); $pathNode.InnerText=$script:DiskPath
    [void]$diskNode.AppendChild($typeNode); [void]$diskNode.AppendChild($pathNode)
    [void]$disks.AppendChild($diskNode)

    $policyNode=$xml.CreateElement('hot_global_only')
    $volumes=$xml.CreateElement('volumes')
    $main=$xml.CreateElement('main')
    $diskRef=$xml.CreateElement('disk'); $diskRef.InnerText='hot_global'
    [void]$main.AppendChild($diskRef); [void]$volumes.AppendChild($main); [void]$policyNode.AppendChild($volumes)
    [void]$policies.AppendChild($policyNode)

    $settings=New-Object Xml.XmlWriterSettings
    $settings.Indent=$true
    $settings.OmitXmlDeclaration=$true
    $settings.Encoding=New-Object Text.UTF8Encoding($false)
    $settings.NewLineChars=[Environment]::NewLine
    $settings.NewLineHandling=[Xml.NewLineHandling]::Replace
    $writer=[Xml.XmlWriter]::Create($OutputPath,$settings)
    try { $xml.Save($writer) } finally { $writer.Close() }
    [IO.File]::AppendAllText($OutputPath,[Environment]::NewLine,(New-Object Text.UTF8Encoding($false)))
}

if($ContractOnly){
    if($script:VhdxMaxBytes -ne 17179869184 -or $script:VhdxMaxMiB -ne 16384){throw 'hot_global capacity constant drifted.'}
    if($script:VhdxPath -ne 'E:\MarkOrbitData\production\clickhouse\hot_global.vhdx'){throw 'hot_global VHDX path drifted.'}
    if($script:MountName -ne 'markorbit_prod_hot_global'){throw 'hot_global mount name drifted.'}
    if($script:DiskName -ne 'hot_global' -or $script:PolicyName -ne 'hot_global_only'){throw 'hot_global ClickHouse identity drifted.'}
    Write-Host 'HOT_GLOBAL_FOUNDATION_PREPARE_CONTRACT_PASS'
    exit 0
}

foreach($required in @('ExpectedMainSha','CapacityPlanPath','ExpectedCapacityPlanSha256')){
    if(-not (Get-Variable -Name $required -ValueOnly)){throw "$required is required."}
}

Push-Location $repoRoot
try {
    $mainSha=Assert-ExactMain $ExpectedMainSha
    $capacity=Assert-CapacityPlan $CapacityPlanPath $ExpectedCapacityPlanSha256
    Assert-ToolingReady
    Assert-TargetRunning
    $baseline=Assert-TargetBaseline
    $e=Get-EState
    if([string]$e.state -ne 'READY'){throw "E reserve is not READY: $($e.state)"}
    if(Test-Path -LiteralPath $script:VhdxPath){throw "hot_global VHDX already exists: $($script:VhdxPath)"}

    $mountProbe=Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','findmnt','-n','-T',$script:MountPath) -AllowFailure
    if($mountProbe.exit_code -eq 0){throw "hot_global mount path is already mounted: $($script:MountPath)"}

    $stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir=[IO.Path]::GetFullPath((Join-Path $EvidenceRoot "hot_global_foundation_prepare_$stamp"))
    $repoFull=[IO.Path]::GetFullPath($repoRoot)
    if($evidenceDir.StartsWith($repoFull,[StringComparison]::OrdinalIgnoreCase)){throw 'EvidenceRoot must be outside the Git worktree.'}
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

    $currentConfig=Get-TargetConfigText
    $currentConfigSha=Get-TargetFileSha $script:TargetConfig
    $currentConfigPath=Join-Path $evidenceDir 'target-config-before.xml'
    [IO.File]::WriteAllText($currentConfigPath,$currentConfig,(New-Object Text.UTF8Encoding($false)))
    $proposedConfigPath=Join-Path $evidenceDir 'target-config-proposed.xml'
    New-ProposedConfig $currentConfig $proposedConfigPath
    $proposedSha=Get-FileSha256 $proposedConfigPath

    [xml]$check=Get-Content -LiteralPath $proposedConfigPath -Raw -Encoding UTF8
    if([string]$check.clickhouse.storage_configuration.disks.hot_global.path -ne $script:DiskPath){throw 'Proposed config hot_global path verification failed.'}
    if([string]$check.clickhouse.storage_configuration.policies.hot_global_only.volumes.main.disk -ne 'hot_global'){throw 'Proposed config hot_global_only verification failed.'}

    $applyPlan=[ordered]@{
        version=$script:PlanVersion
        issue=$script:Issue
        prepared_at=[DateTimeOffset]::UtcNow.ToString('o')
        main_sha=$mainSha
        mutation_authorized=$false
        capacity_plan=[ordered]@{path=$capacity.path;sha256=$capacity.sha256;version=$script:CapacityPlanVersion}
        preflight=[ordered]@{
            e_drive=$e
            vhdx_absent=$true
            mount_absent=$true
            target_distro_running=$true
            target_clickhouse_running=$true
            accepted_disks=@($baseline.disks)
            accepted_policies=@($baseline.policies)
        }
        target=[ordered]@{
            vhdx_path=$script:VhdxPath
            vhdx_type='expandable'
            max_bytes=$script:VhdxMaxBytes
            maximum_mib=$script:VhdxMaxMiB
            ext4_label=$script:Ext4Label
            tooling_distro=$ToolingDistro
            runtime_distro=$script:TargetDistro
            mount_name=$script:MountName
            mount_path=$script:MountPath
            clickhouse_disk_path=$script:DiskPath
            clickhouse_disk=$script:DiskName
            clickhouse_policy=$script:PolicyName
        }
        config=[ordered]@{
            runtime_path=$script:TargetConfig
            before_sha256=$currentConfigSha
            before_evidence_path=$currentConfigPath
            proposed_path=$proposedConfigPath
            proposed_sha256=$proposedSha
            reload_command='SYSTEM RELOAD CONFIG'
        }
        apply_steps=@(
            'create dynamic VHDX with diskpart exact maximum_mib',
            'bare-mount exact VHDX and format exact-one new block device ext4',
            'unmount exact VHDX then named-mount as markorbit_prod_hot_global',
            'create empty clickhouse-data directory root:root mode 0770',
            'replace exact target config only if before SHA still matches',
            'SYSTEM RELOAD CONFIG',
            'verify hot_global disk + hot_global_only policy + zero parts + ext4',
            'verify hot_us and warm_cn baseline identities unchanged',
            'verify E 30pct reserve remains READY'
        )
        forbidden=@(
            'hot_us/warm_cn resize or remount',
            'ClickHouse table CREATE/ALTER/INSERT',
            'data copy/replay',
            'Docker lifecycle mutation',
            'WSL shutdown/unregister',
            'no-argument wsl --unmount',
            'serving cutover'
        )
    }
    $planPath=Join-Path $evidenceDir 'hot_global_foundation_apply_plan.json'
    Write-JsonFile $applyPlan $planPath
    $planSha=Get-FileSha256 $planPath
    $token="GO #823 HOT-GLOBAL $planSha PROVISION-RELOAD-VERIFY"

    Write-Host 'HOT_GLOBAL_FOUNDATION_PREPARE_PASS'
    Write-Host "plan_path=$planPath"
    Write-Host "plan_sha256=$planSha"
    Write-Host "main_sha=$mainSha"
    Write-Host "capacity_plan_sha256=$($capacity.sha256)"
    Write-Host "config_before_sha256=$currentConfigSha"
    Write-Host "config_proposed_sha256=$proposedSha"
    Write-Host "e_reserve_state=$($e.state)"
    Write-Host "authority_token=$token"
}
finally { Pop-Location }
