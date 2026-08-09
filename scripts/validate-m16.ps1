$ErrorActionPreference = "Stop"

Write-Host "Checking M1.6 API and database health..." -ForegroundColor Cyan
$health = Invoke-RestMethod -Method Get -Uri "http://localhost:8080/api/health"
$health | ConvertTo-Json -Depth 10
if ($health.version -ne "M1.6") {
    throw "Unexpected engine version: $($health.version). Expected M1.6."
}
if ($health.postgres -ne "ok" -or $health.clickhouse -ne "ok") {
    throw "Database health check failed."
}

Write-Host "Checking M1.6 core + durable goods schema..." -ForegroundColor Cyan
$schema = Invoke-RestMethod -Method Get -Uri "http://localhost:8080/api/cn/schema"
$required = @(
    "cn_case_current.filing_route",
    "cn_case_current.international_registration_number",
    "cn_case_scope_current.interpretation_complete",
    "cn_case_party_current.relation_key",
    "cn_observed_event.field_name",
    "cn_case_relation_current.relation_type",
    "cn_goods_item_current.goods_item_key",
    "cn_goods_item_current.operational_effect",
    "cn_goods_item_current.first_source_package_id",
    "cn_goods_item_observation.transition_type",
    "cn_goods_scope_lifecycle_current.all_known_goods_inactive",
    "cn_goods_scope_lifecycle_current.all_known_goods_final_inactive",
    "cn_goods_scope_lifecycle_current.code_2_item_count"
)
$available = @{}
foreach ($item in $schema) {
    $available["$($item.table).$($item.name)"] = $true
}
foreach ($field in $required) {
    if (-not $available.ContainsKey($field)) {
        throw "Missing M1.6 field: $field"
    }
}

Write-Host "Checking M1.6 summary surface..." -ForegroundColor Cyan
$summary = Invoke-RestMethod -Method Get -Uri "http://localhost:8080/api/cn/summary"
if ($summary.version -ne "M1.6") {
    throw "Unexpected summary version: $($summary.version)"
}
$tableNames = @($summary.tables | ForEach-Object { $_.table_name })
foreach ($table in @("cn_goods_item_current", "cn_goods_item_observation", "cn_goods_scope_lifecycle_current")) {
    if ($tableNames -notcontains $table) {
        throw "M1.6 summary is missing table: $table"
    }
}
if ($null -eq $summary.goods_lifecycle) {
    throw "M1.6 summary is missing goods_lifecycle metrics."
}

Write-Host "M1.6 runtime, schema, and summary validation passed." -ForegroundColor Green
