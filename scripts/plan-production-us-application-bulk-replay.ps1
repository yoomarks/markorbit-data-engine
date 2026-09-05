[CmdletBinding(DefaultParameterSetName = 'ByEnd')]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMain,

    [ValidateRange(3, 310)]
    [int]$StartSequence = 3,

    [Parameter(Mandatory = $true, ParameterSetName = 'ByEnd')]
    [ValidateRange(3, 310)]
    [int]$EndSequence,

    [Parameter(Mandatory = $true, ParameterSetName = 'ByCount')]
    [ValidateRange(1, 308)]
    [int]$MaxPackages,

    [string]$PythonExe = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Stage2Decision = 'BOUNDED_US_APPLICATION_CANARY_STAGE2_PACKAGE2_ACCEPTED'
$Package2Sha = '96555bf13b6e8c2f2ede3433c88e4c600b7115ef3e4d7d22f28c8263cada60c7'
$Package2Id = 'aec9c8b5-f680-5881-94fb-71a1f8e44152'
$SchemaSha = 'ff801dea29e5f4b146e5e7ca24507abf4d7d498f977af64e1bc2e14267f63795'
$RawRoot = 'F:\MarkOrbitData\raw'

function Require-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Invoke-NativeCapture {
    param([scriptblock]$Command, [string]$Label)
    $lines = @(& $Command 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $joined = ($lines | ForEach-Object { [string]$_ }) -join "`n"
        throw "$Label failed with exit $code`n$joined"
    }
    return @($lines | ForEach-Object { [string]$_ })
}

function Get-ExactSingleLine {
    param([object[]]$Lines, [string]$Label)
    $materialized = @($Lines | ForEach-Object { [string]$_ })
    Require-True ($materialized.Count -eq 1) "$Label returned unexpected line count: $($materialized.Count)"
    return $materialized[0].Trim()
}

function Assert-ExactMain {
    param([string]$Phase)
    Invoke-NativeCapture -Label "git fetch origin main ($Phase)" -Command { & git fetch origin main } | Out-Null
    $branch = Get-ExactSingleLine -Label "git branch ($Phase)" -Lines @(Invoke-NativeCapture -Label "git branch ($Phase)" -Command { & git branch --show-current })
    Require-True ($branch -eq 'main') "Bulk plan must run from local main during $Phase."
    $head = Get-ExactSingleLine -Label "git HEAD ($Phase)" -Lines @(Invoke-NativeCapture -Label "git HEAD ($Phase)" -Command { & git rev-parse HEAD })
    $origin = Get-ExactSingleLine -Label "git origin/main ($Phase)" -Lines @(Invoke-NativeCapture -Label "git origin/main ($Phase)" -Command { & git rev-parse origin/main })
    $expected = $ExpectedMain.ToLowerInvariant()
    Require-True ($head.ToLowerInvariant() -eq $expected) "HEAD mismatch during ${Phase}: expected=$expected actual=$head"
    Require-True ($origin.ToLowerInvariant() -eq $expected) "origin/main mismatch during ${Phase}: expected=$expected actual=$origin"
    $dirty = @(Invoke-NativeCapture -Label "git status ($Phase)" -Command { & git status --porcelain=v1 --untracked-files=normal })
    Require-True ($dirty.Count -eq 0) "Working tree is not exactly clean during $Phase."
}

function Find-AcceptedStage2Receipt {
    param([string]$ReportsRoot)
    $dirs = @(Get-ChildItem -LiteralPath $ReportsRoot -Directory -Filter 'production_us_application_canary_stage2_*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)
    foreach ($dir in $dirs) {
        $path = Join-Path $dir.FullName 'stage2_python_receipt.json'
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        try {
            $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([string]$receipt.decision -ne $Stage2Decision) { continue }
            if ([int]$receipt.package.sequence -ne 2) { continue }
            if ([string]$receipt.package.sha256 -ne $Package2Sha) { continue }
            if ([string]$receipt.package.package_id -ne $Package2Id) { continue }
            if ([string]$receipt.schema.manifest_sha256 -ne $SchemaSha) { continue }
            if ([string]$receipt.journal.state -ne 'COMPLETE') { continue }
            if (-not [bool]$receipt.authority.consumed) { continue }
            if ([bool]$receipt.safety.package_3_executed -or [bool]$receipt.safety.full_corpus_executed -or [bool]$receipt.safety.automatic_next_package) { continue }
            return $path
        }
        catch { continue }
    }
    throw 'No accepted Package 2 Stage 2 Python receipt matches the frozen #526 contract.'
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$ReportsRoot = Join-Path $RepoRoot 'reports'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
$EvidenceDir = Join-Path $ReportsRoot "production_us_application_bulk_plan_$stamp"
$PlanPath = Join-Path $EvidenceDir 'bulk_plan.json'
$WrapperPath = Join-Path $EvidenceDir 'bulk_plan_wrapper.json'

try {
    Require-True (Test-Path -LiteralPath $RawRoot -PathType Container) "Accepted F: Raw root is missing: $RawRoot"
    if ($PSCmdlet.ParameterSetName -eq 'ByEnd') {
        Require-True ($EndSequence -ge $StartSequence) 'EndSequence must be >= StartSequence.'
    }
    else {
        Require-True (($StartSequence + $MaxPackages - 1) -le 310) 'MaxPackages exceeds the accepted sequence-310 source corpus.'
    }

    Assert-ExactMain -Phase 'entry'
    $pythonCommand = Get-Command $PythonExe -ErrorAction Stop
    Require-True ($null -ne $pythonCommand) "Python executable not found: $PythonExe"
    $stage2Receipt = Find-AcceptedStage2Receipt -ReportsRoot $ReportsRoot
    New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null

    $pythonArgs = @(
        '-m', 'app.us.target_bulk_cli', 'plan',
        '--raw-root', $RawRoot,
        '--execution-main', $ExpectedMain.ToLowerInvariant(),
        '--stage2-receipt', $stage2Receipt,
        '--start-sequence', [string]$StartSequence,
        '--output', $PlanPath
    )
    if ($PSCmdlet.ParameterSetName -eq 'ByEnd') {
        $pythonArgs += @('--end-sequence', [string]$EndSequence)
    }
    else {
        $pythonArgs += @('--max-packages', [string]$MaxPackages)
    }
    Invoke-NativeCapture -Label 'US Application bulk plan builder' -Command { & $PythonExe @pythonArgs } | Out-Null
    Require-True (Test-Path -LiteralPath $PlanPath -PathType Leaf) 'Bulk plan JSON was not written.'

    $plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Require-True ([string]$plan.execution_main -eq $ExpectedMain.ToLowerInvariant()) 'Bulk plan execution_main drifted.'
    Require-True ([bool]$plan.read_only -and -not [bool]$plan.production_mutation_authorized) 'Bulk plan incorrectly authorizes production mutation.'
    Require-True ([int]$plan.bridge_sequence -eq 1 -and [int]$plan.accepted_existing_target_sequence -eq 2) 'Bulk plan target continuity contract drifted.'
    Require-True ([int]$plan.start_sequence -eq $StartSequence) 'Bulk plan start sequence drifted.'
    Require-True ([int]$plan.end_sequence -le 310) 'Bulk plan escaped the accepted source corpus.'
    Require-True ([string]$plan.accepted_schema_manifest_sha256 -eq $SchemaSha) 'Bulk plan target schema SHA drifted.'

    Assert-ExactMain -Phase 'exit'
    $wrapper = [ordered]@{
        report_version = 'PRODUCTION_US_APPLICATION_BULK_PLAN_WRAPPER_V1'
        decision = 'US_APPLICATION_TARGET_BULK_PLAN_FROZEN'
        execution_main = $ExpectedMain.ToLowerInvariant()
        stage2_receipt = $stage2Receipt
        plan_path = $PlanPath
        plan_sha256 = [string]$plan.plan_sha256
        inventory_sha256 = [string]$plan.inventory_sha256
        bridge_sequence = 1
        accepted_existing_target_sequence = 2
        start_sequence = [int]$plan.start_sequence
        end_sequence = [int]$plan.end_sequence
        suffix_package_count = [int]$plan.suffix_package_count
        authority_granted = $false
        production_mutation_authorized = $false
    }
    $wrapper | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $WrapperPath -Encoding UTF8

    Write-Host "decision=US_APPLICATION_TARGET_BULK_PLAN_FROZEN"
    Write-Host "evidence_dir=$EvidenceDir"
    Write-Host "plan_path=$PlanPath"
    Write-Host "plan_sha256=$($plan.plan_sha256)"
    Write-Host "inventory_sha256=$($plan.inventory_sha256)"
    Write-Host 'bridge_sequence=1'
    Write-Host 'accepted_existing_target_sequence=2'
    Write-Host "start_sequence=$($plan.start_sequence)"
    Write-Host "end_sequence=$($plan.end_sequence)"
    Write-Host "suffix_package_count=$($plan.suffix_package_count)"
    Write-Host "required_authority_token_candidate=$($plan.required_authority_token)"
    Write-Host 'authority_granted=False'
    Write-Host 'production_mutation_authorized=False'
    exit 0
}
catch {
    Write-Host 'decision=BLOCKED'
    Write-Host "error=$($_.Exception.Message)"
    Write-Host 'production_mutation_authorized=False'
    exit 2
}
