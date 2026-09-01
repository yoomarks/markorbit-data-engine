[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$ColdRoot = 'F:\MarkOrbitData\cold',
    [int64]$MaxSourceBytesToHash = 68719476736,
    [double]$HashCoverageThresholdPercent = 90.0,
    [string]$EvidenceRoot = 'reports'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Assert-ExactMain([string]$Phase) {
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) {
        throw "Exact-main mismatch at $Phase."
    }
    $dirty = @(git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect git working tree.' }
    if (@($dirty | Where-Object { $_.Trim() }).Count -ne 0) {
        throw "Working tree must be clean at $Phase."
    }
}

function Get-RawDataPath {
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        throw '.env is missing; RAW_DATA_PATH cannot be resolved.'
    }
    $line = Get-Content -LiteralPath $envPath -Encoding UTF8 |
        Where-Object { $_ -match '^\s*RAW_DATA_PATH\s*=' } |
        Select-Object -First 1
    if (-not $line) { throw 'RAW_DATA_PATH is not configured in .env.' }
    $value = (($line -split '=', 2)[1]).Trim().Trim('"').Trim("'")
    if (-not [System.IO.Path]::IsPathRooted($value)) { $value = Join-Path $repoRoot $value }
    $full = [System.IO.Path]::GetFullPath($value).TrimEnd('\')
    if (-not (Test-Path -LiteralPath $full -PathType Container)) {
        throw "Configured RAW_DATA_PATH does not exist: $full"
    }
    return (Resolve-Path -LiteralPath $full).Path.TrimEnd('\')
}

function Get-RelativeChild([string]$Root, [string]$FullPath) {
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    return $FullPath.Substring($rootFull.Length).TrimStart('\')
}

function Get-FirstFamily([string]$RelativePath) {
    $index = $RelativePath.IndexOf('\')
    if ($index -ge 0) { return $RelativePath.Substring(0, $index) }
    return '__root__'
}

function Add-FamilyMetric([hashtable]$Map, [string]$Family, [int64]$Length) {
    if (-not $Map.ContainsKey($Family)) {
        $Map[$Family] = [ordered]@{ family=$Family; file_count=[int64]0; total_bytes=[int64]0 }
    }
    $Map[$Family].file_count = [int64]$Map[$Family].file_count + 1
    $Map[$Family].total_bytes = [int64]$Map[$Family].total_bytes + $Length
}

function Get-NameSizeKey([string]$Name, [int64]$Length) {
    return ($Name.ToLowerInvariant() + '|' + [string]$Length)
}

function Get-Sha256Hex([string]$Path) {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $hash = $sha.ComputeHash($stream)
        }
        finally {
            $sha.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
    return ([System.BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
}

try {
    Write-Host '===== RAW/COLD PARITY PROFILE ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Raw/Cold parity must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Raw/Cold parity requires elevated Administrator PowerShell.'
    }
    if ($MaxSourceBytesToHash -lt 0) { throw 'MaxSourceBytesToHash must be non-negative.' }
    if ($HashCoverageThresholdPercent -lt 0 -or $HashCoverageThresholdPercent -gt 100) {
        throw 'HashCoverageThresholdPercent must be between 0 and 100.'
    }

    $sourceRoot = Get-RawDataPath
    $coldFull = [System.IO.Path]::GetFullPath($ColdRoot).TrimEnd('\')
    if (-not (Test-Path -LiteralPath $coldFull -PathType Container)) {
        throw "Cold root does not exist: $coldFull"
    }
    $coldFull = (Resolve-Path -LiteralPath $coldFull).Path.TrimEnd('\')

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "raw_cold_parity_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

    Write-Host 'parity_stage=source_metadata'
    $sourceFiles = @()
    $sourceByRelative = @{}
    $sourceNameSizeKeys = @{}
    $sourceFamilies = @{}
    [int64]$sourceCount = 0
    [int64]$sourceBytes = 0
    foreach ($filePath in [System.IO.Directory]::EnumerateFiles($sourceRoot, '*', [System.IO.SearchOption]::AllDirectories)) {
        $info = New-Object System.IO.FileInfo($filePath)
        $relative = Get-RelativeChild $sourceRoot $filePath
        $entry = [ordered]@{
            path=$filePath
            relative=$relative
            name=$info.Name
            length=[int64]$info.Length
        }
        $sourceFiles += $entry
        $sourceByRelative[$relative] = $entry
        $sourceNameSizeKeys[(Get-NameSizeKey $info.Name ([int64]$info.Length))] = $true
        Add-FamilyMetric $sourceFamilies (Get-FirstFamily $relative) ([int64]$info.Length)
        $sourceCount++
        $sourceBytes += [int64]$info.Length
    }
    Write-Host "source_root=$sourceRoot"
    Write-Host "source_file_count=$sourceCount"
    Write-Host "source_total_bytes=$sourceBytes"

    Write-Host 'parity_stage=cold_metadata'
    $coldExact = @{}
    $coldNameSize = @{}
    $coldFamilies = @{}
    [int64]$coldCount = 0
    [int64]$coldBytes = 0
    foreach ($filePath in [System.IO.Directory]::EnumerateFiles($coldFull, '*', [System.IO.SearchOption]::AllDirectories)) {
        $info = New-Object System.IO.FileInfo($filePath)
        $relative = Get-RelativeChild $coldFull $filePath
        [int64]$length = $info.Length
        $coldCount++
        $coldBytes += $length
        Add-FamilyMetric $coldFamilies (Get-FirstFamily $relative) $length

        if ($sourceByRelative.ContainsKey($relative)) {
            $coldExact[$relative] = [ordered]@{ path=$filePath; relative=$relative; name=$info.Name; length=$length }
        }
        $nameSizeKey = Get-NameSizeKey $info.Name $length
        if ($sourceNameSizeKeys.ContainsKey($nameSizeKey)) {
            if (-not $coldNameSize.ContainsKey($nameSizeKey)) { $coldNameSize[$nameSizeKey] = @() }
            if (@($coldNameSize[$nameSizeKey]).Count -lt 3) {
                $coldNameSize[$nameSizeKey] = @($coldNameSize[$nameSizeKey]) + @([ordered]@{ path=$filePath; relative=$relative; name=$info.Name; length=$length })
            }
        }
    }
    Write-Host "cold_root=$coldFull"
    Write-Host "cold_file_count=$coldCount"
    Write-Host "cold_total_bytes=$coldBytes"
    foreach ($family in @($coldFamilies.Values | Sort-Object @{Expression='total_bytes';Descending=$true}, @{Expression='family';Descending=$false})) {
        Write-Host "cold_family=$($family.family)`tfiles=$($family.file_count)`tbytes=$($family.total_bytes)"
    }

    Write-Host 'parity_stage=candidate_matching'
    $claimedTargets = @{}
    $pairs = @()
    $unmatched = @()
    [int64]$candidateBytes = 0
    foreach ($source in $sourceFiles) {
        $target = $null
        $method = $null
        if ($coldExact.ContainsKey($source.relative)) {
            $exact = $coldExact[$source.relative]
            if ([int64]$exact.length -eq [int64]$source.length -and -not $claimedTargets.ContainsKey($exact.path)) {
                $target = $exact
                $method = 'exact_relative_size'
            }
        }
        if ($null -eq $target) {
            $key = Get-NameSizeKey $source.name ([int64]$source.length)
            $available = @()
            if ($coldNameSize.ContainsKey($key)) {
                $available = @($coldNameSize[$key] | Where-Object { -not $claimedTargets.ContainsKey($_.path) })
            }
            if ($available.Count -eq 1) {
                $target = $available[0]
                $method = 'unique_name_size'
            }
        }

        if ($null -ne $target) {
            $claimedTargets[$target.path] = $true
            $pairs += [ordered]@{
                source_path=$source.path
                source_relative=$source.relative
                source_length=[int64]$source.length
                target_path=$target.path
                target_relative=$target.relative
                match_method=$method
                source_sha256=$null
                target_sha256=$null
                hash_equal=$null
            }
            $candidateBytes += [int64]$source.length
        }
        else {
            $key = Get-NameSizeKey $source.name ([int64]$source.length)
            $candidateCount = if ($coldNameSize.ContainsKey($key)) { @($coldNameSize[$key]).Count } else { 0 }
            $unmatched += [ordered]@{
                source_relative=$source.relative
                source_length=[int64]$source.length
                name_size_candidate_count=$candidateCount
            }
        }
    }

    [int64]$candidateCountTotal = @($pairs).Count
    $byteCoverage = if ($sourceBytes -gt 0) { [math]::Round((100.0 * $candidateBytes / $sourceBytes), 4) } else { 100.0 }
    $fileCoverage = if ($sourceCount -gt 0) { [math]::Round((100.0 * $candidateCountTotal / $sourceCount), 4) } else { 100.0 }
    Write-Host "candidate_file_count=$candidateCountTotal"
    Write-Host "candidate_source_bytes=$candidateBytes"
    Write-Host "candidate_byte_coverage_percent=$byteCoverage"
    Write-Host "candidate_file_coverage_percent=$fileCoverage"
    Write-Host "unmatched_source_file_count=$(@($unmatched).Count)"

    Write-Host 'parity_stage=conditional_hash'
    $hashAttempted = $false
    [int64]$verifiedCount = 0
    [int64]$verifiedBytes = 0
    [int64]$hashMismatchCount = 0
    $hashEligible = ($candidateCountTotal -gt 0 -and $byteCoverage -ge $HashCoverageThresholdPercent)
    $hashWithinBudget = ($candidateBytes -le $MaxSourceBytesToHash)
    Write-Host "hash_eligible=$hashEligible"
    Write-Host "hash_within_budget=$hashWithinBudget"
    Write-Host "max_source_bytes_to_hash=$MaxSourceBytesToHash"

    if ($hashEligible -and $hashWithinBudget) {
        $hashAttempted = $true
        [int64]$hashedSourceBytes = 0
        [int64]$hashedFiles = 0
        foreach ($pair in $pairs) {
            $sourceHash = Get-Sha256Hex $pair.source_path
            $targetHash = Get-Sha256Hex $pair.target_path
            $equal = ($sourceHash -eq $targetHash)
            $pair.source_sha256 = $sourceHash
            $pair.target_sha256 = $targetHash
            $pair.hash_equal = $equal
            $hashedFiles++
            $hashedSourceBytes += [int64]$pair.source_length
            if ($equal) {
                $verifiedCount++
                $verifiedBytes += [int64]$pair.source_length
            }
            else {
                $hashMismatchCount++
            }
            if (($hashedFiles % 100) -eq 0 -or $hashedFiles -eq $candidateCountTotal) {
                Write-Host "hash_progress_files=$hashedFiles/$candidateCountTotal`thash_progress_source_bytes=$hashedSourceBytes/$candidateBytes"
            }
        }
    }

    $decision = $null
    $recommendedNextAction = $null
    if ($sourceCount -eq 0) {
        $decision = 'RAW_COLD_PARITY_SOURCE_EMPTY'
        $recommendedNextAction = 'REVIEW_RAW_PATH_CONFIGURATION'
    }
    elseif ($candidateCountTotal -eq 0) {
        $decision = 'RAW_COLD_PARITY_NO_REUSABLE_EQUIVALENT'
        $recommendedNextAction = 'DESIGN_FORWARD_COPY_TO_F_RAW_ROOT'
    }
    elseif (-not $hashEligible) {
        $decision = 'RAW_COLD_PARITY_INSUFFICIENT_METADATA_COVERAGE'
        $recommendedNextAction = 'DESIGN_FORWARD_COPY_TO_F_RAW_ROOT'
    }
    elseif (-not $hashWithinBudget) {
        $decision = 'RAW_COLD_PARITY_HASH_BUDGET_BLOCKED'
        $recommendedNextAction = 'DESIGN_BOUNDED_HASH_PLAN'
    }
    elseif ($hashMismatchCount -gt 0) {
        $decision = 'RAW_COLD_PARITY_MISMATCH'
        $recommendedNextAction = 'DESIGN_FORWARD_COPY_TO_F_RAW_ROOT'
    }
    elseif ($verifiedCount -eq $sourceCount) {
        $decision = 'RAW_COLD_PARITY_EQUIVALENT'
        $recommendedNextAction = 'DESIGN_NO_COPY_ENV_CUTOVER'
    }
    else {
        $decision = 'RAW_COLD_PARITY_PARTIAL_VERIFIED'
        $recommendedNextAction = 'DESIGN_FORWARD_COPY_TO_F_RAW_ROOT'
    }

    $receipt = [ordered]@{
        receipt_version='RAW_COLD_PARITY_V1'
        decision=$decision
        recommended_next_action=$recommendedNextAction
        read_only=$true
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        source=[ordered]@{
            root=$sourceRoot
            drive_root=[System.IO.Path]::GetPathRoot($sourceRoot)
            file_count=$sourceCount
            total_bytes=$sourceBytes
            families=@($sourceFamilies.Values | Sort-Object @{Expression='total_bytes';Descending=$true}, @{Expression='family';Descending=$false})
        }
        cold=[ordered]@{
            root=$coldFull
            drive_root=[System.IO.Path]::GetPathRoot($coldFull)
            file_count=$coldCount
            total_bytes=$coldBytes
            families=@($coldFamilies.Values | Sort-Object @{Expression='total_bytes';Descending=$true}, @{Expression='family';Descending=$false})
        }
        metadata_match=[ordered]@{
            candidate_file_count=$candidateCountTotal
            candidate_source_bytes=$candidateBytes
            byte_coverage_percent=$byteCoverage
            file_coverage_percent=$fileCoverage
            unmatched_source_file_count=@($unmatched).Count
        }
        hash=[ordered]@{
            eligible=$hashEligible
            attempted=$hashAttempted
            max_source_bytes_to_hash=$MaxSourceBytesToHash
            verified_file_count=$verifiedCount
            verified_source_bytes=$verifiedBytes
            mismatch_file_count=$hashMismatchCount
        }
        pairs=$pairs
        unmatched=$unmatched
        safety=[ordered]@{
            migration_authorized=$false
            env_change_authorized=$false
            raw_copy_authorized=$false
            raw_move_authorized=$false
            raw_delete_authorized=$false
            vhdx_mutation_authorized=$false
            docker_restart_authorized=$false
            clickhouse_mutation_authorized=$false
            corpus_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
        mutations_performed=[ordered]@{
            env_change=$false
            raw_copy=$false
            raw_move=$false
            raw_delete=$false
            vhdx_mutation=$false
            docker_restart=$false
            clickhouse_mutation=$false
            corpus_replay=$false
        }
    }

    $receiptPath = Join-Path $evidenceDir 'raw_cold_parity.json'
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== RAW/COLD PARITY RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "recommended_next_action=$recommendedNextAction"
    Write-Host "hash_attempted=$hashAttempted"
    Write-Host "verified_file_count=$verifiedCount"
    Write-Host "verified_source_bytes=$verifiedBytes"
    Write-Host "hash_mismatch_count=$hashMismatchCount"
    Write-Host 'migration_authorized=False'
    Write-Host 'env_change_authorized=False'
    Write-Host 'raw_copy_authorized=False'
    Write-Host 'raw_move_authorized=False'
    Write-Host 'raw_delete_authorized=False'
    Write-Host 'vhdx_mutation_performed=False'
    Write-Host 'docker_restart_performed=False'
    Write-Host 'clickhouse_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'RAW_COLD_PARITY_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
