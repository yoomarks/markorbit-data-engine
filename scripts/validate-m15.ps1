$ErrorActionPreference = "Stop"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

Write-Host "Checking Docker services..." -ForegroundColor Cyan
docker compose ps
Assert-LastExitCode "docker compose ps"

Write-Host "Checking API health..." -ForegroundColor Cyan
$health = Invoke-RestMethod -Method Get -Uri "http://localhost:8080/api/health"
$health | ConvertTo-Json -Depth 10
if ($health.version -ne "M1.5") {
    throw "Unexpected engine version: $($health.version)"
}
if ($health.postgres -ne "ok" -or $health.clickhouse -ne "ok") {
    throw "Database health check failed."
}

Write-Host "Checking required M1.5 columns..." -ForegroundColor Cyan
$schema = Invoke-RestMethod -Method Get -Uri "http://localhost:8080/api/cn/schema"
$required = @(
    "cn_case_current.filing_route",
    "cn_case_current.international_registration_number",
    "cn_case_current.exclusive_period_raw",
    "cn_case_scope_current.unmapped_status_item_count",
    "cn_case_scope_current.interpretation_complete",
    "cn_case_party_current.relation_key",
    "cn_observed_event.field_name",
    "cn_case_relation_current.relation_type"
)
$available = @{}
foreach ($item in $schema) {
    $available["$($item.table).$($item.name)"] = $true
}
foreach ($field in $required) {
    if (-not $available.ContainsKey($field)) {
        throw "Missing M1.5 field: $field"
    }
}

Write-Host "M1.5 runtime and schema validation passed." -ForegroundColor Green
