[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$TargetRawRoot = 'F:\MarkOrbitData\raw',
    [string]$LegacyRawRoot = 'D:\yoomarks\markorbit-data-engine\raw_data',
    [string]$EvidenceRoot = 'reports',
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $lines = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code ${exitCode}."
    }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
}

function Invoke-DockerComposeConfigJson {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'docker.exe'
    $psi.Arguments = 'compose --profile mark-image --profile qcc config --format json'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    return [ordered]@{ exit_code=[int]$process.ExitCode; stdout=$stdout; stderr_present=[bool](-not [string]::IsNullOrWhiteSpace($stderr)) }
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $originMain -ne $expected) { throw "Exact main drift detected during $Phase." }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}

function Get-DotEnvValues {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*=(.*)$'
    $values = @()
    foreach ($line in @($Lines)) {
        $match = [regex]::Match([string]$line, $pattern)
        if ($match.Success) { $values += $match.Groups[1].Value.Trim().Trim('"').Trim("'") }
    }
    return @($values)
}

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Normalize-DockerBindSource([string]$Source) {
    if ([string]::IsNullOrWhiteSpace($Source)) { return '' }
    if ([System.IO.Path]::IsPathRooted($Source) -and $Source -match '^[A-Za-z]:[\\/]') {
        return Normalize-HostPath $Source
    }
    foreach ($pattern in @(
        '^/run/desktop/mnt/host/(?<drive>[A-Za-z])/(?<rest>.*)$',
        '^/host_mnt/(?<drive>[A-Za-z])/(?<rest>.*)$',
        '^/mnt/host/(?<drive>[A-Za-z])/(?<rest>.*)$'
    )) {
        $match = [regex]::Match($Source, $pattern)
        if ($match.Success) {
            $drive = $match.Groups['drive'].Value.ToUpperInvariant()
            $rest = $match.Groups['rest'].Value.Replace('/', '\')
            return Normalize-HostPath ("${drive}:\$rest")
        }
    }
    return ''
}

function Get-TreeManifest([string]$Root) {
    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
    $prefix = $resolvedRoot + '\'
    $items = @()
    foreach ($path in [System.IO.Directory]::EnumerateFiles($resolvedRoot, '*', [System.IO.SearchOption]::AllDirectories)) {
        $fullPath = [System.IO.Path]::GetFullPath($path)
        $relativePath = $fullPath.Substring($prefix.Length)
        $info = New-Object System.IO.FileInfo($fullPath)
        $items += [pscustomobject]@{ relative_path=$relativePath; key=$relativePath.ToLowerInvariant(); length=[int64]$info.Length }
    }
    return @($items | Sort-Object relative_path)
}

function Get-ManifestStats([object[]]$Manifest) {
    $bytes = [int64]0
    foreach ($item in @($Manifest)) { $bytes += [int64]$item.length }
    return [ordered]@{ file_count=[int64]@($Manifest).Count; total_bytes=$bytes }
}

function Compare-MetadataExact([object[]]$SourceManifest, [object[]]$TargetManifest) {
    $sourceMap = @{}
    foreach ($item in @($SourceManifest)) { $sourceMap[$item.key] = $item }
    $targetMap = @{}
    foreach ($item in @($TargetManifest)) { $targetMap[$item.key] = $item }
    if ($sourceMap.Count -ne $targetMap.Count) { return $false }
    foreach ($key in $sourceMap.Keys) {
        if (-not $targetMap.ContainsKey($key)) { return $false }
        if ([int64]$sourceMap[$key].length -ne [int64]$targetMap[$key].length) { return $false }
    }
    return $true
}

function Get-RunningComposeServiceCount([string]$Service) {
    $probe = Invoke-NativeText 'docker' @('compose','ps','-q',$Service) -AllowFailure
    if ($probe['exit_code'] -ne 0) { return [ordered]@{ probe_ok=$false; count=[int64]-1 } }
    $ids = @($probe['lines'] | Where-Object { $_.Trim() })
    return [ordered]@{ probe_ok=$true; count=[int64]$ids.Count }
}

function Get-ProductionClickHouseHealth {
    $containerProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($containerProbe['lines']) -join '').Trim()
    if (-not $containerId) { return [ordered]@{ ready=$false; health=$null } }
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim()
    $ready = [bool]($healthProbe['exit_code'] -eq 0 -and $health -eq 'healthy' -and $sqlProbe['exit_code'] -eq 0 -and ((@($sqlProbe['lines']) -join '').Trim() -eq '1'))
    return [ordered]@{ ready=$ready; health=$health }
}

function Get-ComposeService([object]$Config, [string]$Service) {
    $property = @($Config.services.PSObject.Properties | Where-Object { $_.Name -eq $Service })
    if ($property.Count -ne 1) { return $null }
    return $property[0].Value
}

function Get-ComposeBindSource([object]$Config, [string]$Service, [string]$Target) {
    $serviceConfig = Get-ComposeService $Config $Service
    if ($null -eq $serviceConfig) { return $null }
    $matches = @($serviceConfig.volumes | Where-Object { $_.type -eq 'bind' -and $_.target -eq $Target })
    if ($matches.Count -ne 1) { return $null }
    return [string]$matches[0].source
}

$probeName = $null
$probeCreated = $false
$probeRemoved = $false
try {
    Write-Host '===== RAW BIND RUNTIME PROBE ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Raw bind runtime probe must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Raw bind runtime probe requires elevated Administrator PowerShell.' }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "raw_bind_runtime_probe_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
    $targetFullPath = Normalize-HostPath $TargetRawRoot
    $legacyFullPath = Normalize-HostPath $LegacyRawRoot
    if ($targetFullPath -ne (Normalize-HostPath 'F:\MarkOrbitData\raw')) { throw 'Target Raw root must remain F:\MarkOrbitData\raw.' }

    Write-Host 'probe_stage=cutover_state_preflight'
    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    $envLines = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    $rawValues = @(Get-DotEnvValues -Lines $envLines -Name 'RAW_DATA_PATH')
    $visualRawValues = @(Get-DotEnvValues -Lines $envLines -Name 'VISUAL_RAW_PATH')
    $envCutoverReady = [bool]($rawValues.Count -eq 1 -and $rawValues[0] -eq 'F:/MarkOrbitData/raw' -and $visualRawValues.Count -eq 1 -and $visualRawValues[0] -eq 'F:/MarkOrbitData/raw')
    Write-Host "env_cutover_ready=$envCutoverReady"

    if (-not (Test-Path -LiteralPath $legacyFullPath -PathType Container)) { throw "Legacy Raw source missing: $legacyFullPath" }
    if (-not (Test-Path -LiteralPath $targetFullPath -PathType Container)) { throw "F Raw target missing: $targetFullPath" }
    $sourceBefore = @(Get-TreeManifest $legacyFullPath)
    $targetBefore = @(Get-TreeManifest $targetFullPath)
    $sourceStats = Get-ManifestStats $sourceBefore
    $targetStats = Get-ManifestStats $targetBefore
    $metadataReady = Compare-MetadataExact $sourceBefore $targetBefore
    Write-Host "source_file_count=$($sourceStats['file_count'])"
    Write-Host "source_total_bytes=$($sourceStats['total_bytes'])"
    Write-Host "target_file_count=$($targetStats['file_count'])"
    Write-Host "target_total_bytes=$($targetStats['total_bytes'])"
    Write-Host "metadata_parity_exact=$metadataReady"

    $rawConsumerServices = @('api','worker','mark-image-worker','qcc-acquisition')
    $consumerProbeFailed = $false
    $runningConsumersBefore = [int64]0
    foreach ($service in $rawConsumerServices) {
        $state = Get-RunningComposeServiceCount $service
        Write-Host "raw_consumer_service_before=$service probe_ok=$($state['probe_ok']) running_count=$($state['count'])"
        if (-not [bool]$state['probe_ok']) { $consumerProbeFailed = $true }
        elseif ([int64]$state['count'] -gt 0) { $runningConsumersBefore += [int64]$state['count'] }
    }
    $productionBefore = Get-ProductionClickHouseHealth
    $acceptedBeforeProbe = Invoke-NativeText 'docker' @('volume','inspect','markorbit-data-engine_clickhouse_data') -AllowFailure
    $acceptedBefore = ($acceptedBeforeProbe['exit_code'] -eq 0)
    Write-Host "running_raw_consumer_count_before=$runningConsumersBefore"
    Write-Host "production_clickhouse_ready_before=$($productionBefore['ready'])"
    Write-Host "production_clickhouse_health_before=$($productionBefore['health'])"
    Write-Host "accepted_volume_present_before=$acceptedBefore"

    Write-Host 'probe_stage=compose_resolution_preflight'
    $configProbe = Invoke-DockerComposeConfigJson
    $composeReady = $false
    $visualProcessedExpected = Normalize-HostPath (Join-Path $repoRoot 'raw_data\visual_processed')
    if ($configProbe['exit_code'] -eq 0) {
        try {
            $config = $configProbe['stdout'] | ConvertFrom-Json
            $composeBlockers = @()
            foreach ($service in @('api','worker','mark-image-worker','qcc-acquisition')) {
                $resolved = Normalize-HostPath (Get-ComposeBindSource -Config $config -Service $service -Target '/data/raw')
                Write-Host "compose_bind_service=$service target=/data/raw source=$resolved"
                if ($resolved -ne $targetFullPath) { $composeBlockers += "RAW_BIND_NOT_ON_F:$service" }
            }
            foreach ($service in @('api','worker','mark-image-worker')) {
                $resolved = Normalize-HostPath (Get-ComposeBindSource -Config $config -Service $service -Target '/data/visual-raw')
                Write-Host "compose_bind_service=$service target=/data/visual-raw source=$resolved"
                if ($resolved -ne $targetFullPath) { $composeBlockers += "VISUAL_RAW_BIND_NOT_ON_F:$service" }
            }
            $composeReady = ($composeBlockers.Count -eq 0)
        }
        catch { $composeReady = $false }
    }
    Write-Host "compose_resolution_ready=$composeReady"

    $readyForApply = [bool]($envCutoverReady -and $metadataReady -and -not $consumerProbeFailed -and $runningConsumersBefore -eq 0 -and [bool]$productionBefore['ready'] -and $acceptedBefore -and $composeReady)
    Write-Host "ready_for_runtime_probe=$readyForApply"

    $runtimeMountReady = $false
    $runtimeReadReady = $false
    $rawRuntimeCount = [int64]0
    $rawRuntimeBytes = [int64]0
    $visualRuntimeCount = [int64]0
    $visualRuntimeBytes = [int64]0
    $runtimeRawSource = ''
    $runtimeVisualRawSource = ''
    $runtimeVisualProcessedSource = ''

    if ($Apply -and $readyForApply) {
        Write-Host 'probe_stage=transient_api_container'
        $probeName = "markorbit-raw-bind-probe-$timestamp"
        $runProbe = Invoke-NativeText 'docker' @(
            'compose','--profile','mark-image','--profile','qcc','run','--detach','--no-deps',
            '--name',$probeName,'--entrypoint','python','api','-c','import time; time.sleep(300)'
        ) -AllowFailure
        if ($runProbe['exit_code'] -ne 0) { throw 'Transient api probe container could not be created without dependencies.' }
        $probeCreated = $true

        $inspectProbe = Invoke-NativeText 'docker' @('inspect',$probeName) -AllowFailure
        if ($inspectProbe['exit_code'] -ne 0) { throw 'Transient api probe container inspect failed.' }
        $inspectJson = (@($inspectProbe['lines']) -join [Environment]::NewLine) | ConvertFrom-Json
        $container = @($inspectJson)[0]
        $runtimeRawMount = @($container.Mounts | Where-Object { $_.Destination -eq '/data/raw' })
        $runtimeVisualRawMount = @($container.Mounts | Where-Object { $_.Destination -eq '/data/visual-raw' })
        $runtimeVisualProcessedMount = @($container.Mounts | Where-Object { $_.Destination -eq '/data/visual-processed' })
        if ($runtimeRawMount.Count -eq 1) { $runtimeRawSource = Normalize-DockerBindSource ([string]$runtimeRawMount[0].Source) }
        if ($runtimeVisualRawMount.Count -eq 1) { $runtimeVisualRawSource = Normalize-DockerBindSource ([string]$runtimeVisualRawMount[0].Source) }
        if ($runtimeVisualProcessedMount.Count -eq 1) { $runtimeVisualProcessedSource = Normalize-DockerBindSource ([string]$runtimeVisualProcessedMount[0].Source) }
        Write-Host "runtime_bind_target=/data/raw source=$runtimeRawSource"
        Write-Host "runtime_bind_target=/data/visual-raw source=$runtimeVisualRawSource"
        Write-Host "runtime_bind_target=/data/visual-processed source=$runtimeVisualProcessedSource"
        $runtimeMountReady = [bool]($runtimeRawSource -eq $targetFullPath -and $runtimeVisualRawSource -eq $targetFullPath -and $runtimeVisualProcessedSource -eq $visualProcessedExpected)

        Write-Host 'probe_stage=read_only_container_walk'
        $walkCode = "import os;`ndef stats(root):`n c=b=0`n for d,_,fs in os.walk(root):`n  for f in fs:`n   p=os.path.join(d,f); c+=1; b+=os.path.getsize(p)`n return c,b`nr=stats('/data/raw'); v=stats('/data/visual-raw'); print(f'{r[0]}|{r[1]}|{v[0]}|{v[1]}')"
        $walkProbe = Invoke-NativeText 'docker' @('exec',$probeName,'python','-c',$walkCode) -AllowFailure
        if ($walkProbe['exit_code'] -eq 0) {
            $walkLine = (@($walkProbe['lines'] | Where-Object { $_ -match '^\d+\|\d+\|\d+\|\d+$' }) | Select-Object -Last 1)
            if ($walkLine) {
                $parts = $walkLine.Split('|')
                $rawRuntimeCount = [int64]$parts[0]
                $rawRuntimeBytes = [int64]$parts[1]
                $visualRuntimeCount = [int64]$parts[2]
                $visualRuntimeBytes = [int64]$parts[3]
                $runtimeReadReady = [bool](
                    $rawRuntimeCount -eq [int64]$targetStats['file_count'] -and
                    $rawRuntimeBytes -eq [int64]$targetStats['total_bytes'] -and
                    $visualRuntimeCount -eq [int64]$targetStats['file_count'] -and
                    $visualRuntimeBytes -eq [int64]$targetStats['total_bytes']
                )
            }
        }
        Write-Host "runtime_raw_file_count=$rawRuntimeCount"
        Write-Host "runtime_raw_total_bytes=$rawRuntimeBytes"
        Write-Host "runtime_visual_raw_file_count=$visualRuntimeCount"
        Write-Host "runtime_visual_raw_total_bytes=$visualRuntimeBytes"
        Write-Host "runtime_mount_ready=$runtimeMountReady"
        Write-Host "runtime_read_ready=$runtimeReadReady"
    }

    Write-Host 'probe_stage=cleanup_and_post_invariants'
    if ($probeCreated -and $probeName) {
        $removeProbe = Invoke-NativeText 'docker' @('rm','-f',$probeName) -AllowFailure
        $probeRemoved = ($removeProbe['exit_code'] -eq 0)
    }
    $runningConsumersAfter = [int64]0
    $consumerProbeFailedAfter = $false
    foreach ($service in $rawConsumerServices) {
        $state = Get-RunningComposeServiceCount $service
        Write-Host "raw_consumer_service_after=$service probe_ok=$($state['probe_ok']) running_count=$($state['count'])"
        if (-not [bool]$state['probe_ok']) { $consumerProbeFailedAfter = $true }
        elseif ([int64]$state['count'] -gt 0) { $runningConsumersAfter += [int64]$state['count'] }
    }
    $productionAfter = Get-ProductionClickHouseHealth
    $acceptedAfterProbe = Invoke-NativeText 'docker' @('volume','inspect','markorbit-data-engine_clickhouse_data') -AllowFailure
    $acceptedAfter = ($acceptedAfterProbe['exit_code'] -eq 0)
    $sourceAfter = @(Get-TreeManifest $legacyFullPath)
    $targetAfter = @(Get-TreeManifest $targetFullPath)
    $sourceStable = Compare-MetadataExact $sourceBefore $sourceAfter
    $targetStable = Compare-MetadataExact $targetBefore $targetAfter
    $envHashAfter = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    $envUnchanged = ($envHashBefore -eq $envHashAfter)
    Write-Host "probe_container_created=$probeCreated"
    Write-Host "probe_container_removed=$probeRemoved"
    Write-Host "running_raw_consumer_count_after=$runningConsumersAfter"
    Write-Host "production_clickhouse_ready_after=$($productionAfter['ready'])"
    Write-Host "production_clickhouse_health_after=$($productionAfter['health'])"
    Write-Host "accepted_volume_present_after=$acceptedAfter"
    Write-Host "source_manifest_stable=$sourceStable"
    Write-Host "target_manifest_stable=$targetStable"
    Write-Host "env_unchanged=$envUnchanged"

    $blockers = @()
    if (-not $readyForApply) { $blockers += 'RUNTIME_PROBE_PREFLIGHT_NOT_READY' }
    if ($Apply -and -not $probeCreated) { $blockers += 'TRANSIENT_PROBE_CONTAINER_NOT_CREATED' }
    if ($Apply -and -not $runtimeMountReady) { $blockers += 'RUNTIME_BIND_SOURCE_NOT_ACCEPTED' }
    if ($Apply -and -not $runtimeReadReady) { $blockers += 'RUNTIME_READ_ONLY_WALK_MISMATCH' }
    if ($probeCreated -and -not $probeRemoved) { $blockers += 'TRANSIENT_PROBE_CONTAINER_NOT_REMOVED' }
    if ($consumerProbeFailedAfter -or $runningConsumersAfter -ne 0) { $blockers += 'RAW_CONSUMER_STATE_CHANGED' }
    if (-not [bool]$productionAfter['ready']) { $blockers += 'PRODUCTION_CLICKHOUSE_NOT_HEALTHY_AFTER' }
    if (-not $acceptedAfter) { $blockers += 'ACCEPTED_CLICKHOUSE_VOLUME_MISSING_AFTER' }
    if (-not $sourceStable) { $blockers += 'LEGACY_SOURCE_METADATA_CHANGED' }
    if (-not $targetStable) { $blockers += 'F_RAW_METADATA_CHANGED' }
    if (-not $envUnchanged) { $blockers += 'ENV_CHANGED_DURING_RUNTIME_PROBE' }

    $accepted = [bool]($Apply -and $blockers.Count -eq 0)
    $decision = if ($accepted) { 'RAW_BIND_RUNTIME_PROBE_GO' } elseif ($readyForApply -and -not $Apply) { 'RAW_BIND_RUNTIME_PROBE_READY_FOR_APPLY' } else { 'RAW_BIND_RUNTIME_PROBE_BLOCKED' }
    $nextGate = if ($accepted) { 'PRODUCTION_HOT_WARM_SIZING_PLAN' } else { 'NONE' }
    $deleteBlockers = @('VISUAL_PROCESSED_PATH_UNDER_LEGACY_D_RAW','LEGACY_D_RAW_CLEANUP_NOT_YET_PLANNED')

    $receipt = [ordered]@{
        schema='RAW_BIND_RUNTIME_PROBE_V1'
        generated_at_utc=(Get-Date).ToUniversalTime().ToString('o')
        decision=$decision
        runtime_probe_accepted=$accepted
        env_cutover_ready=$envCutoverReady
        metadata_parity_exact=$metadataReady
        compose_resolution_ready=$composeReady
        runtime_raw_source=$runtimeRawSource
        runtime_visual_raw_source=$runtimeVisualRawSource
        runtime_visual_processed_source=$runtimeVisualProcessedSource
        runtime_mount_ready=$runtimeMountReady
        runtime_read_ready=$runtimeReadReady
        runtime_raw_file_count=$rawRuntimeCount
        runtime_raw_total_bytes=$rawRuntimeBytes
        runtime_visual_raw_file_count=$visualRuntimeCount
        runtime_visual_raw_total_bytes=$visualRuntimeBytes
        probe_container_created=$probeCreated
        probe_container_removed=$probeRemoved
        running_raw_consumer_count_before=$runningConsumersBefore
        running_raw_consumer_count_after=$runningConsumersAfter
        production_clickhouse_ready_before=[bool]$productionBefore['ready']
        production_clickhouse_ready_after=[bool]$productionAfter['ready']
        accepted_volume_present_before=$acceptedBefore
        accepted_volume_present_after=$acceptedAfter
        source_manifest_stable=$sourceStable
        target_manifest_stable=$targetStable
        env_unchanged=$envUnchanged
        blockers=@($blockers)
        d_source_delete_blockers=@($deleteBlockers)
        next_gate=$nextGate
        raw_delete_authorized=$false
        raw_move_authorized=$false
        env_change_authorized=$false
        worker_start_authorized=$false
        docker_restart_performed=$false
        docker_recreate_performed=$false
        clickhouse_mutation_performed=$false
        vhdx_mutation_performed=$false
        wsl_mutation_performed=$false
        corpus_replay_performed=$false
        us_package_2_authorized=$false
        us_bulk_authorized=$false
    }
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceDir 'raw_bind_runtime_probe.json') -Encoding UTF8

    Write-Host '===== RAW BIND RUNTIME PROBE RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "runtime_probe_accepted=$accepted"
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "next_gate=$nextGate"
    Write-Host 'raw_delete_authorized=False'
    Write-Host "blocker_count=$($blockers.Count)"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host "d_source_delete_blocker_count=$($deleteBlockers.Count)"
    foreach ($deleteBlocker in $deleteBlockers) { Write-Host "d_source_delete_blocker=$deleteBlocker" }
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'RAW_BIND_RUNTIME_PROBE_DONE'
    Assert-ExactMain 'exit'
}
finally {
    if ($probeCreated -and -not $probeRemoved -and $probeName) {
        try { & docker rm -f $probeName 2>$null | Out-Null } catch { }
    }
    Pop-Location
}
