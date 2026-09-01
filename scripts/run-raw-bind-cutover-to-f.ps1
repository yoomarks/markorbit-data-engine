[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$TargetRawRoot = 'F:\MarkOrbitData\raw',
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
    return [ordered]@{
        exit_code=[int]$process.ExitCode
        stdout=$stdout
        stderr_present=[bool](-not [string]::IsNullOrWhiteSpace($stderr))
    }
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift detected during $Phase."
    }
    if (git status --porcelain) {
        throw "Working tree must be clean during $Phase."
    }
}

function Get-DotEnvValues {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [string[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*=(.*)$'
    $values = @()
    foreach ($line in @($Lines)) {
        $match = [regex]::Match([string]$line, $pattern)
        if (-not $match.Success) { continue }
        $values += $match.Groups[1].Value.Trim().Trim('"').Trim("'")
    }
    return @($values)
}

function Get-Utf8TextFromBytes([byte[]]$Bytes) {
    $hasBom = [bool](
        $Bytes.Length -ge 3 -and
        $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF
    )
    $offset = if ($hasBom) { 3 } else { 0 }
    $encoding = New-Object System.Text.UTF8Encoding($false, $true)
    $text = $encoding.GetString($Bytes, $offset, $Bytes.Length - $offset)
    return [ordered]@{ text=$text; has_bom=$hasBom }
}

function Get-Utf8Bytes([string]$Text, [bool]$WithBom) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [byte[]]$body = $encoding.GetBytes($Text)
    if (-not $WithBom) { return $body }
    [byte[]]$bom = @(0xEF,0xBB,0xBF)
    return [byte[]]($bom + $body)
}

function Get-EnvLines([string]$Text) {
    return @([regex]::Split($Text, '\r\n|\n|\r'))
}

function Get-NonTargetEnvText([string]$Text) {
    $kept = @()
    foreach ($line in @(Get-EnvLines $Text)) {
        if ($line -match '^\s*(RAW_DATA_PATH|VISUAL_RAW_PATH)\s*=') { continue }
        $kept += $line
    }
    return ($kept -join "`n")
}

function Set-JointRawEnvText([string]$Text, [string]$ComposePath) {
    $rawPattern = '(?m)^(?<prefix>[ \t]*RAW_DATA_PATH[ \t]*=).*$'
    $visualPattern = '(?m)^(?<prefix>[ \t]*VISUAL_RAW_PATH[ \t]*=).*$'
    $rawRegex = New-Object System.Text.RegularExpressions.Regex($rawPattern)
    $visualRegex = New-Object System.Text.RegularExpressions.Regex($visualPattern)
    if ($rawRegex.Matches($Text).Count -ne 1) { throw 'RAW_DATA_PATH entry count changed before mutation.' }
    if ($visualRegex.Matches($Text).Count -gt 1) { throw 'VISUAL_RAW_PATH entry count changed before mutation.' }

    $updated = $rawRegex.Replace(
        $Text,
        [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $m.Groups['prefix'].Value + $ComposePath },
        1
    )
    if ($visualRegex.Matches($updated).Count -eq 1) {
        $updated = $visualRegex.Replace(
            $updated,
            [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $m.Groups['prefix'].Value + $ComposePath },
            1
        )
    }
    else {
        $newline = if ($Text.Contains("`r`n")) { "`r`n" } else { "`n" }
        $hadTrailingNewline = $updated.EndsWith("`r`n") -or $updated.EndsWith("`n") -or $updated.EndsWith("`r")
        if ($updated.Length -gt 0 -and -not $hadTrailingNewline) { $updated += $newline }
        $updated += "VISUAL_RAW_PATH=$ComposePath"
        if ($hadTrailingNewline) { $updated += $newline }
    }
    return $updated
}

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
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
    $ready = [bool](
        $healthProbe['exit_code'] -eq 0 -and
        $health -eq 'healthy' -and
        $sqlProbe['exit_code'] -eq 0 -and
        ((@($sqlProbe['lines']) -join '').Trim() -eq '1')
    )
    return [ordered]@{ ready=$ready; health=$health }
}

try {
    Write-Host '===== JOINT RAW BIND CUTOVER TO F ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Joint Raw bind cutover must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Joint Raw bind cutover requires elevated Administrator PowerShell.'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "raw_bind_cutover_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
    $targetFullPath = Normalize-HostPath $TargetRawRoot
    $targetComposePath = 'F:/MarkOrbitData/raw'
    if ($targetFullPath -ne (Normalize-HostPath 'F:\MarkOrbitData\raw')) { throw 'Target Raw root must remain F:\MarkOrbitData\raw.' }

    Write-Host 'cutover_stage=mandatory_preflight'
    $preflightPath = Join-Path $PSScriptRoot 'preflight-raw-bind-cutover-to-f.ps1'
    $preflight = Invoke-NativeText 'powershell.exe' @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$preflightPath,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-TargetRawRoot',$TargetRawRoot,
        '-EvidenceRoot',$EvidenceRoot
    ) -AllowFailure
    foreach ($line in @($preflight['lines'])) { Write-Host $line }
    $preflightReady = [bool](
        $preflight['exit_code'] -eq 0 -and
        @($preflight['lines'] | Where-Object { $_ -eq 'decision=RAW_BIND_CUTOVER_PREFLIGHT_READY' }).Count -eq 1 -and
        @($preflight['lines'] | Where-Object { $_ -eq 'blocker_count=0' }).Count -eq 1
    )
    Write-Host "mandatory_preflight_ready=$preflightReady"

    $envPath = Join-Path $repoRoot '.env'
    [byte[]]$envBytesBefore = [System.IO.File]::ReadAllBytes($envPath)
    $envHashBefore = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    $decoded = Get-Utf8TextFromBytes $envBytesBefore
    $envTextBefore = [string]$decoded['text']
    $envHadBom = [bool]$decoded['has_bom']
    $nonTargetBefore = Get-NonTargetEnvText $envTextBefore
    $envLinesBefore = @(Get-EnvLines $envTextBefore)
    $rawValuesBefore = @(Get-DotEnvValues -Lines $envLinesBefore -Name 'RAW_DATA_PATH')
    $visualRawValuesBefore = @(Get-DotEnvValues -Lines $envLinesBefore -Name 'VISUAL_RAW_PATH')
    $visualProcessedValuesBefore = @(Get-DotEnvValues -Lines $envLinesBefore -Name 'VISUAL_PROCESSED_PATH')

    $readyForApply = [bool](
        $preflightReady -and
        $rawValuesBefore.Count -eq 1 -and
        $visualRawValuesBefore.Count -le 1 -and
        $visualProcessedValuesBefore.Count -le 1
    )
    Write-Host "ready_for_apply=$readyForApply"

    $envWritePerformed = $false
    $composeValidationPassed = $false
    $runtimeStatePreserved = $false
    $productionInvariantPreserved = $false
    $rollbackPerformed = $false
    $rollbackVerified = $false
    $postBlockers = @()
    $resolvedBindRows = @()

    if ($Apply -and $readyForApply) {
        Write-Host 'cutover_stage=env_joint_update'
        $envTextAfter = Set-JointRawEnvText -Text $envTextBefore -ComposePath $targetComposePath
        if ((Get-NonTargetEnvText $envTextAfter) -ne $nonTargetBefore) {
            throw 'Non-target .env content changed during in-memory cutover construction.'
        }
        [byte[]]$envBytesAfter = Get-Utf8Bytes -Text $envTextAfter -WithBom $envHadBom

        try {
            [System.IO.File]::WriteAllBytes($envPath, $envBytesAfter)
            $envWritePerformed = $true
        }
        catch {
            try {
                [System.IO.File]::WriteAllBytes($envPath, $envBytesBefore)
                $rollbackPerformed = $true
                $rollbackVerified = ((Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash -eq $envHashBefore)
            }
            catch { }
            if (-not $rollbackVerified) { throw 'Environment write failed and exact rollback could not be verified.' }
            throw
        }

        $envTextOnDisk = [string](Get-Utf8TextFromBytes ([System.IO.File]::ReadAllBytes($envPath)))['text']
        $envLinesOnDisk = @(Get-EnvLines $envTextOnDisk)
        $rawValuesAfter = @(Get-DotEnvValues -Lines $envLinesOnDisk -Name 'RAW_DATA_PATH')
        $visualRawValuesAfter = @(Get-DotEnvValues -Lines $envLinesOnDisk -Name 'VISUAL_RAW_PATH')
        $visualProcessedValuesAfter = @(Get-DotEnvValues -Lines $envLinesOnDisk -Name 'VISUAL_PROCESSED_PATH')
        if ($rawValuesAfter.Count -ne 1 -or $rawValuesAfter[0] -ne $targetComposePath) { $postBlockers += 'RAW_DATA_PATH_POST_WRITE_INVALID' }
        if ($visualRawValuesAfter.Count -ne 1 -or $visualRawValuesAfter[0] -ne $targetComposePath) { $postBlockers += 'VISUAL_RAW_PATH_POST_WRITE_INVALID' }
        if (($visualProcessedValuesAfter -join "`n") -ne ($visualProcessedValuesBefore -join "`n")) { $postBlockers += 'VISUAL_PROCESSED_PATH_CHANGED' }
        if ((Get-NonTargetEnvText $envTextOnDisk) -ne $nonTargetBefore) { $postBlockers += 'NON_TARGET_ENV_CONTENT_CHANGED' }

        Write-Host 'cutover_stage=compose_resolution_validation'
        $configProbe = Invoke-DockerComposeConfigJson
        if ($configProbe['exit_code'] -ne 0) {
            $postBlockers += 'DOCKER_COMPOSE_CONFIG_FAILED'
        }
        else {
            try {
                $config = $configProbe['stdout'] | ConvertFrom-Json
                $expectedRawServices = @('api','worker','mark-image-worker','qcc-acquisition')
                $expectedVisualServices = @('api','worker','mark-image-worker')
                foreach ($service in $expectedRawServices) {
                    $source = Get-ComposeBindSource -Config $config -Service $service -Target '/data/raw'
                    $normalized = Normalize-HostPath $source
                    $resolvedBindRows += [ordered]@{ service=$service; target='/data/raw'; source=$normalized }
                    Write-Host "resolved_bind_service=$service target=/data/raw source=$normalized"
                    if ($normalized -ne $targetFullPath) { $postBlockers += "RAW_BIND_NOT_ON_F:$service" }
                }
                foreach ($service in $expectedVisualServices) {
                    $source = Get-ComposeBindSource -Config $config -Service $service -Target '/data/visual-raw'
                    $normalized = Normalize-HostPath $source
                    $resolvedBindRows += [ordered]@{ service=$service; target='/data/visual-raw'; source=$normalized }
                    Write-Host "resolved_bind_service=$service target=/data/visual-raw source=$normalized"
                    if ($normalized -ne $targetFullPath) { $postBlockers += "VISUAL_RAW_BIND_NOT_ON_F:$service" }
                }

                $visualProcessedExpected = if ($visualProcessedValuesBefore.Count -eq 1 -and -not [string]::IsNullOrWhiteSpace([string]$visualProcessedValuesBefore[0])) {
                    Normalize-HostPath ([string]$visualProcessedValuesBefore[0])
                }
                else {
                    Normalize-HostPath (Join-Path $repoRoot 'raw_data\visual_processed')
                }
                foreach ($service in $expectedVisualServices) {
                    $source = Get-ComposeBindSource -Config $config -Service $service -Target '/data/visual-processed'
                    $normalized = Normalize-HostPath $source
                    $resolvedBindRows += [ordered]@{ service=$service; target='/data/visual-processed'; source=$normalized }
                    Write-Host "resolved_bind_service=$service target=/data/visual-processed source=$normalized"
                    if ($normalized -ne $visualProcessedExpected) { $postBlockers += "VISUAL_PROCESSED_BIND_CHANGED:$service" }
                }
                $composeValidationPassed = ($postBlockers.Count -eq 0)
            }
            catch {
                $postBlockers += 'DOCKER_COMPOSE_CONFIG_JSON_VALIDATION_FAILED'
            }
        }

        Write-Host 'cutover_stage=runtime_invariants_after'
        $rawConsumerServices = @('api','worker','mark-image-worker','qcc-acquisition')
        $consumerProbeFailed = $false
        $runningRawConsumerCount = [int64]0
        foreach ($service in $rawConsumerServices) {
            $state = Get-RunningComposeServiceCount $service
            Write-Host "raw_consumer_service_after=$service probe_ok=$($state['probe_ok']) running_count=$($state['count'])"
            if (-not [bool]$state['probe_ok']) { $consumerProbeFailed = $true }
            elseif ([int64]$state['count'] -gt 0) { $runningRawConsumerCount += [int64]$state['count'] }
        }
        $runtimeStatePreserved = (-not $consumerProbeFailed -and $runningRawConsumerCount -eq 0)
        if (-not $runtimeStatePreserved) { $postBlockers += 'RAW_CONSUMER_STATE_CHANGED_AFTER_ENV_UPDATE' }

        $production = Get-ProductionClickHouseHealth
        $acceptedVolumeProbe = Invoke-NativeText 'docker' @('volume','inspect','markorbit-data-engine_clickhouse_data') -AllowFailure
        $productionInvariantPreserved = [bool]($production['ready'] -and $acceptedVolumeProbe['exit_code'] -eq 0)
        Write-Host "production_clickhouse_ready_after=$($production['ready'])"
        Write-Host "production_clickhouse_health_after=$($production['health'])"
        Write-Host "accepted_volume_present_after=$($acceptedVolumeProbe['exit_code'] -eq 0)"
        if (-not $productionInvariantPreserved) { $postBlockers += 'PRODUCTION_INVARIANT_FAILED_AFTER_ENV_UPDATE' }

        if ($postBlockers.Count -gt 0) {
            Write-Host 'cutover_stage=automatic_env_rollback'
            [System.IO.File]::WriteAllBytes($envPath, $envBytesBefore)
            $rollbackPerformed = $true
            $rollbackVerified = ((Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash -eq $envHashBefore)
            Write-Host "rollback_verified=$rollbackVerified"
            if (-not $rollbackVerified) { throw 'Post-cutover validation failed and exact .env rollback could not be verified.' }
        }
    }
    elseif ($Apply) {
        Write-Host 'cutover_stage=apply_blocked'
    }

    $applyAccepted = [bool](
        $Apply -and
        $readyForApply -and
        $envWritePerformed -and
        $postBlockers.Count -eq 0 -and
        $composeValidationPassed -and
        $runtimeStatePreserved -and
        $productionInvariantPreserved -and
        -not $rollbackPerformed
    )

    if ($Apply) {
        $decision = if ($applyAccepted) { 'RAW_BIND_CUTOVER_APPLY_GO' } else { 'RAW_BIND_CUTOVER_BLOCKED' }
    }
    else {
        $decision = if ($readyForApply) { 'RAW_BIND_CUTOVER_READY_FOR_APPLY' } else { 'RAW_BIND_CUTOVER_BLOCKED' }
    }
    $nextGate = if ($applyAccepted) { 'RAW_BIND_RUNTIME_PROBE' } else { 'NONE' }
    $deleteBlockers = @('RAW_BIND_RUNTIME_PROBE_NOT_YET_ACCEPTED','VISUAL_PROCESSED_PATH_UNDER_LEGACY_D_RAW')

    $envHashFinal = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    $receipt = [ordered]@{
        schema='RAW_BIND_CUTOVER_APPLY_V1'
        generated_at_utc=(Get-Date).ToUniversalTime().ToString('o')
        decision=$decision
        apply_requested=[bool]$Apply
        ready_for_apply=$readyForApply
        apply_accepted=$applyAccepted
        mandatory_preflight_ready=$preflightReady
        target_raw_root=$targetFullPath
        proposed_RAW_DATA_PATH=$targetComposePath
        proposed_VISUAL_RAW_PATH=$targetComposePath
        proposed_VISUAL_PROCESSED_PATH='UNCHANGED'
        env_hash_before=$envHashBefore
        env_hash_final=$envHashFinal
        env_write_performed=$envWritePerformed
        compose_validation_passed=$composeValidationPassed
        resolved_binds=@($resolvedBindRows)
        raw_consumer_state_preserved=$runtimeStatePreserved
        production_invariant_preserved=$productionInvariantPreserved
        rollback_performed=$rollbackPerformed
        rollback_verified=$rollbackVerified
        blockers=@($postBlockers)
        next_gate=$nextGate
        d_source_delete_blockers=@($deleteBlockers)
        raw_delete_authorized=$false
        visual_processed_migration_authorized=$false
        docker_recreate_performed=$false
        docker_restart_performed=$false
        clickhouse_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        vhdx_mutation_performed=$false
        wsl_mutation_performed=$false
        corpus_replay_performed=$false
        us_package_2_authorized=$false
        us_bulk_authorized=$false
    }
    $reportPath = Join-Path $evidenceDir 'raw_bind_cutover_apply.json'
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host '===== JOINT RAW BIND CUTOVER TO F RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "ready_for_apply=$readyForApply"
    Write-Host "apply_accepted=$applyAccepted"
    Write-Host "env_write_performed=$envWritePerformed"
    Write-Host "compose_validation_passed=$composeValidationPassed"
    Write-Host "runtime_state_preserved=$runtimeStatePreserved"
    Write-Host "production_invariant_preserved=$productionInvariantPreserved"
    Write-Host "rollback_performed=$rollbackPerformed"
    Write-Host "rollback_verified=$rollbackVerified"
    Write-Host "next_gate=$nextGate"
    Write-Host 'raw_delete_authorized=False'
    Write-Host 'docker_recreate_performed=False'
    Write-Host "blocker_count=$($postBlockers.Count)"
    foreach ($blocker in $postBlockers) { Write-Host "blocker=$blocker" }
    Write-Host "d_source_delete_blocker_count=$($deleteBlockers.Count)"
    foreach ($deleteBlocker in $deleteBlockers) { Write-Host "d_source_delete_blocker=$deleteBlocker" }
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'RAW_BIND_CUTOVER_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
