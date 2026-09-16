[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $false)]
    [string]$AcceptedDesignReceiptPath,
    [string]$PythonExe = 'python',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:ReceiptVersion = 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_REVIEW_V1'
$script:PlanVersion = 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_PLAN_V1'
$script:DesignReceiptVersion = 'US_SECONDARY_FAMILY_MIGRATION_DESIGN_V1'
$script:DesignReadyDecision = 'US_SECONDARY_FAMILY_MIGRATION_DESIGN_READY'
$script:AcceptedDesignReceiptSha256 = '23fbe1fcf79acaabfb19b0a73e60302cbb3680de924cb7848297ab7b8e57bd50'
$script:AcceptedDesignMainSha = '6f9eac978395520d23de5cfbbdcfee86b0bd5aba'
$script:TargetDisk = 'hot_us'
$script:TargetPolicy = 'hot_us_only'
function Invoke-NativeText {
    param([string]$Command, [AllowEmptyString()][AllowEmptyCollection()][string[]]$Arguments, [switch]$AllowFailure)
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $lines = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command failed with exit code ${exitCode}: $($lines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift during $Phase. expected=$expected head=$head origin_main=$originMain"
    }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}
function Get-StringSha256([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        $hash = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-JsonFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Get-CanonicalJson([object]$Object) {
    return ($Object | ConvertTo-Json -Depth 16 -Compress)
}
function Assert-DesignReceipt([object]$Receipt, [string]$Label) {
    if ([string]$Receipt.receipt_version -ne $script:DesignReceiptVersion) { throw "$Label version mismatch." }
    if ([string]$Receipt.decision -ne $script:DesignReadyDecision) { throw "$Label is not READY." }
    if ([string]$Receipt.next_gate -ne 'TARGET_SCHEMA_APPLY_REVIEW') { throw "$Label next gate drifted." }
    if (-not [bool]$Receipt.read_only -or [bool]$Receipt.mutation_performed) { throw "$Label safety state drifted." }
    if (@($Receipt.blockers).Count -ne 0) { throw "$Label contains blockers." }
    if (-not [bool]$Receipt.repository_schema.live_matches_repository_contract) { throw "$Label repo/live schema mismatch." }
    if ([string]$Receipt.target_design.disk -ne $script:TargetDisk) { throw "$Label target disk drifted." }
    if ([string]$Receipt.target_design.storage_policy -ne $script:TargetPolicy) { throw "$Label target policy drifted." }
    if ([int]$Receipt.target_design.target_tables_currently_absent -ne 8) { throw "$Label target absence drifted." }
    if ([bool]$Receipt.target_design.ddl_execution_performed) { throw "$Label unexpectedly executed DDL." }
    if (@($Receipt.tables).Count -ne 8) { throw "$Label table count drifted." }
    if (-not [bool]$Receipt.capacity_basis.recommended_30pct_current_footprint_fits) { throw "$Label lost 30 percent reserve fit." }
    foreach ($name in @('target_schema_apply_authorized','insert_or_copy_authorized','serving_cutover_authorized','source_delete_authorized')) {
        if ([bool]$Receipt.constraints.$name) { throw "$Label unexpectedly authorizes $name." }
    }
}

function Get-DesignTableIdentity([object]$Receipt) {
    return @(
        $Receipt.tables | Sort-Object { [int]$_.migration_order } | ForEach-Object {
            "$([int]$_.migration_order)|$([string]$_.family)|$([string]$_.table)|$([string]$_.target_create_table_query_sha256)"
        }
    )
}
function Resolve-AcceptedDesignReceipt {
    if ([string]::IsNullOrWhiteSpace($AcceptedDesignReceiptPath)) {
        throw 'AcceptedDesignReceiptPath is required outside ContractOnly.'
    }
    $path = [System.IO.Path]::GetFullPath($AcceptedDesignReceiptPath)
    $sha = Get-FileSha256 $path
    if ($sha -ne $script:AcceptedDesignReceiptSha256) {
        throw "Accepted design receipt SHA mismatch: expected=$($script:AcceptedDesignReceiptSha256) actual=$sha"
    }
    $receipt = Read-JsonFile $path 'Accepted design receipt'
    Assert-DesignReceipt $receipt 'Accepted design receipt'
    if ([string]$receipt.main_sha -ne $script:AcceptedDesignMainSha) { throw 'Accepted design main SHA drifted.' }
    return [ordered]@{ path=$path; sha256=$sha; receipt=$receipt }
}

function Resolve-FreshDesignReceipt([string]$EvidenceDir) {
    $designRoot = Join-Path $EvidenceDir 'fresh_design'
    $designScript = Join-Path $repoRoot 'scripts\design-us-secondary-family-hot-migration.ps1'
    if (-not (Test-Path -LiteralPath $designScript -PathType Leaf)) { throw 'Design operator is missing.' }
    $probe = Invoke-NativeText 'powershell.exe' @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$designScript,
        '-ExpectedMainSha',$ExpectedMainSha,'-PythonExe',$PythonExe,'-EvidenceRoot',$designRoot
    )
    $paths = @($probe.lines | Where-Object { $_ -like 'receipt_path=*' } | ForEach-Object { $_.Substring('receipt_path='.Length).Trim() })
    if ($paths.Count -ne 1) { throw "Fresh design did not emit exactly one receipt path; observed=$($paths.Count)" }
    $path = [System.IO.Path]::GetFullPath($paths[0])
    $receipt = Read-JsonFile $path 'Fresh design receipt'
    Assert-DesignReceipt $receipt 'Fresh design receipt'
    if ([string]$receipt.main_sha -ne $ExpectedMainSha.ToLowerInvariant()) { throw 'Fresh design main SHA drifted.' }
    return [ordered]@{ path=$path; sha256=(Get-FileSha256 $path); receipt=$receipt }
}
function Build-ApplyReviewPlan([object]$Accepted, [object]$Fresh) {
    $acceptedIds = @(Get-DesignTableIdentity $Accepted)
    $freshIds = @(Get-DesignTableIdentity $Fresh)
    if (($acceptedIds -join "`n") -ne ($freshIds -join "`n")) { throw 'Accepted/fresh target DDL identity drifted.' }

    $steps = @()
    foreach ($table in @($Fresh.tables | Sort-Object { [int]$_.migration_order })) {
        $ddl = [string]$table.target_create_table_query
        if ($ddl -notmatch "storage_policy = 'hot_us_only'") { throw "Target DDL policy missing: $($table.table)" }
        if ($ddl -match '(?i)IF NOT EXISTS') { throw "Target DDL must fail closed on collision: $($table.table)" }
        $sha = Get-StringSha256 $ddl
        if ($sha -ne [string]$table.target_create_table_query_sha256) { throw "Target DDL SHA drifted: $($table.table)" }
        $steps += [ordered]@{
            order=[int]$table.migration_order
            family=[string]$table.family
            table=[string]$table.table
            ddl_sha256=$sha
            ddl=$ddl
        }
    }

    return [ordered]@{
        plan_version=$script:PlanVersion
        review_issue=688
        review_main_sha=$ExpectedMainSha.ToLowerInvariant()
        accepted_design_receipt_sha256=$script:AcceptedDesignReceiptSha256
        accepted_design_main_sha=$script:AcceptedDesignMainSha
        target_runtime=[string]$Fresh.target_design.runtime
        target_disk=$script:TargetDisk
        target_storage_policy=$script:TargetPolicy
        schema_apply_steps=@($steps)
        preflight=@(
            'exact_main_and_review_plan_identity',
            'accepted_and_fresh_design_schema_identity_match',
            'all_eight_target_tables_absent',
            'hot_us_only_policy_identity_unchanged',
            'recommended_30pct_current_footprint_reserve_fits',
            'source_remains_authoritative_and_retained'
        )
        per_create_verify=@(
            'created_table_exact_schema_and_ddl_identity',
            'created_table_storage_policy_hot_us_only',
            'created_table_active_parts_zero',
            'source_runtime_unchanged',
            'no_copy_or_serving_action_started'
        )
        partial_failure=[ordered]@{
            behavior='HALT_AND_FREEZE_PARTIAL_EMPTY_SCHEMA_STATE'
            auto_drop_allowed=$false
            silent_continue_allowed=$false
            ordinary_retry_allowed=$false
            remediation_review_required=$true
            created_empty_tables_must_be_reported=$true
        }
        success_next_gate='TARGET_TO_SOURCE_CONNECTIVITY_PREFLIGHT'
        source_cleanup_allowed=$false
        serving_cutover_allowed=$false
        data_copy_allowed=$false
        rollback_drop_allowed=$false
    }
}

function Get-RequiredAuthorityToken([string]$PlanSha) {
    return "GO #688 US secondary family target schema apply $PlanSha"
}
function Invoke-ContractFixture {
    $ddl = "CREATE TABLE markorbit_facts.us_assignment_record_history (`id` String) ENGINE = MergeTree ORDER BY id SETTINGS storage_policy = 'hot_us_only', index_granularity = 8192"
    $ddlSha = Get-StringSha256 $ddl
    $table = [pscustomobject][ordered]@{
        migration_order=1
        family='ASSIGNMENT'
        table='us_assignment_record_history'
        target_create_table_query_sha256=$ddlSha
        target_create_table_query=$ddl
    }
    $design = [pscustomobject][ordered]@{
        tables=@($table)
        target_design=[pscustomobject][ordered]@{ runtime='MarkOrbit-ClickHouse' }
    }
    $plan1 = Build-ApplyReviewPlan $design $design
    $json1 = Get-CanonicalJson $plan1
    $sha1 = Get-StringSha256 $json1
    $plan2 = Build-ApplyReviewPlan $design $design
    $sha2 = Get-StringSha256 (Get-CanonicalJson $plan2)
    if ($sha1 -ne $sha2) { throw 'Canonical plan hash is not deterministic.' }
    $token = Get-RequiredAuthorityToken $sha1
    if ($token -ne "GO #688 US secondary family target schema apply $sha1") { throw 'Authority token format drifted.' }
    if ([bool]$plan1.partial_failure.auto_drop_allowed) { throw 'Auto-drop must remain disabled.' }
    Write-Host 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_REVIEW_CONTRACT_PASS'
    Write-Host "plan_sha256=$sha1"
    Write-Host "required_authority_token=$token"
    Write-Host 'schema_apply_executed=False'
}

if ($ContractOnly) {
    try { Invoke-ContractFixture }
    finally { Pop-Location }
    return
}
try {
    Assert-ExactMain 'review-entry'
    $pythonCommand = Get-Command $PythonExe -ErrorAction Stop
    if (-not $pythonCommand) { throw "Python executable not found: $PythonExe" }

    $root = if ([System.IO.Path]::IsPathRooted($EvidenceRoot)) {
        [System.IO.Path]::GetFullPath($EvidenceRoot)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $repoRoot $EvidenceRoot))
    }
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir = Join-Path $root "us_secondary_family_schema_apply_review_$stamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

    $accepted = Resolve-AcceptedDesignReceipt
    $fresh = Resolve-FreshDesignReceipt $evidenceDir
    $plan = Build-ApplyReviewPlan $accepted.receipt $fresh.receipt
    $planCanonical = Get-CanonicalJson $plan
    $planSha = Get-StringSha256 $planCanonical
    $authorityToken = Get-RequiredAuthorityToken $planSha
    Assert-ExactMain 'pre-receipt'

    $planPath = Join-Path $evidenceDir 'schema_apply_plan.json'
    $receiptPath = Join-Path $evidenceDir 'review_receipt.json'
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($planPath, ($plan | ConvertTo-Json -Depth 16), $utf8)
    $receipt = [ordered]@{
        receipt_version=$script:ReceiptVersion
        generated_at=(Get-Date).ToUniversalTime().ToString('o')
        main_sha=$ExpectedMainSha.ToLowerInvariant()
        decision='US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_REVIEW_READY'
        next_gate='TARGET_SCHEMA_APPLY_AUTHORITY_REQUIRED'
        read_only=$true
        mutation_performed=$false
        schema_apply_executed=$false
        accepted_design=[ordered]@{
            path=$accepted.path
            sha256=$accepted.sha256
            main_sha=[string]$accepted.receipt.main_sha
        }
        fresh_design=[ordered]@{
            path=$fresh.path
            sha256=$fresh.sha256
            main_sha=[string]$fresh.receipt.main_sha
            current_target_absent_count=[int]$fresh.receipt.target_design.target_tables_currently_absent
            current_30pct_reserve_fits=[bool]$fresh.receipt.capacity_basis.recommended_30pct_current_footprint_fits
        }
        plan=[ordered]@{
            path=$planPath
            sha256=$planSha
            version=$script:PlanVersion
            step_count=@($plan.schema_apply_steps).Count
        }
        required_authority_token=$authorityToken
        authority_consumed=$false
        apply_authorized=$false
        partial_failure_behavior=[string]$plan.partial_failure.behavior
        blockers=@()
    }
    [System.IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 12), $utf8)

    Write-Host "receipt_version=$($script:ReceiptVersion)"
    Write-Host 'decision=US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_REVIEW_READY'
    Write-Host 'next_gate=TARGET_SCHEMA_APPLY_AUTHORITY_REQUIRED'
    Write-Host "plan_sha256=$planSha"
    Write-Host "schema_apply_step_count=$(@($plan.schema_apply_steps).Count)"
    Write-Host "required_authority_token=$authorityToken"
    Write-Host 'authority_consumed=False'
    Write-Host 'schema_apply_executed=False'
    Write-Host 'apply_authorized=False'
    Write-Host "plan_path=$planPath"
    Write-Host "receipt_path=$receiptPath"
}
finally {
    Pop-Location
}
