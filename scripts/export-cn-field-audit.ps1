$ErrorActionPreference = "Stop"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$reportDir = Join-Path (Get-Location) "reports\cn_field_audit_m15_$timestamp"
$tempDir = Join-Path $env:TEMP "markorbit_cn_audit_$timestamp"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Run-ClickHouseQuery {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Query,
        [ValidateSet("CSVWithNames", "JSONEachRow")][string]$Format = "CSVWithNames"
    )
    Write-Host "ClickHouse: $Name"
    $queryPath = Join-Path $tempDir "$Name.sql"
    $containerQuery = "/tmp/markorbit_$timestamp`_$Name.sql"
    $containerOutput = "/tmp/markorbit_$timestamp`_$Name"
    [System.IO.File]::WriteAllText($queryPath, $Query.Trim() + "`nFORMAT $Format`n", $utf8NoBom)
    docker compose cp $queryPath "clickhouse:$containerQuery"
    Assert-LastExitCode "copy ClickHouse query $Name"
    docker compose exec -T clickhouse sh -lc "clickhouse-client --user \"`$CLICKHOUSE_USER\" --password \"`$CLICKHOUSE_PASSWORD\" --multiquery < '$containerQuery' > '$containerOutput'"
    Assert-LastExitCode "ClickHouse query $Name"
    docker compose cp "clickhouse:$containerOutput" (Join-Path $reportDir $Name)
    Assert-LastExitCode "copy ClickHouse output $Name"
    docker compose exec -T clickhouse rm -f $containerQuery $containerOutput | Out-Null
}

function Run-PostgresQuery {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Query
    )
    Write-Host "PostgreSQL: $Name"
    $queryPath = Join-Path $tempDir "$Name.sql"
    $containerQuery = "/tmp/markorbit_$timestamp`_$Name.sql"
    $containerOutput = "/tmp/markorbit_$timestamp`_$Name"
    [System.IO.File]::WriteAllText($queryPath, $Query.Trim() + "`n", $utf8NoBom)
    docker compose cp $queryPath "postgres:$containerQuery"
    Assert-LastExitCode "copy PostgreSQL query $Name"
    docker compose exec -T postgres sh -lc "psql -v ON_ERROR_STOP=1 -U \"`$POSTGRES_USER\" -d \"`$POSTGRES_DB\" --csv -f '$containerQuery' > '$containerOutput'"
    Assert-LastExitCode "PostgreSQL query $Name"
    docker compose cp "postgres:$containerOutput" (Join-Path $reportDir $Name)
    Assert-LastExitCode "copy PostgreSQL output $Name"
    docker compose exec -T postgres rm -f $containerQuery $containerOutput | Out-Null
}

Run-ClickHouseQuery "01_clickhouse_columns.csv" @"
SELECT database, table, position, name, type, default_kind, default_expression, comment
FROM system.columns
WHERE database = 'markorbit_facts'
ORDER BY table, position
"@

Run-ClickHouseQuery "02_table_counts.csv" @"
SELECT table_name, row_count FROM
(
    SELECT 'cn_case_current' AS table_name, count() AS row_count FROM markorbit_facts.cn_case_current FINAL WHERE is_deleted = 0
    UNION ALL SELECT 'cn_case_scope_current', count() FROM markorbit_facts.cn_case_scope_current FINAL WHERE is_deleted = 0
    UNION ALL SELECT 'cn_case_party_current', count() FROM markorbit_facts.cn_case_party_current FINAL WHERE is_deleted = 0 AND is_current = 1
    UNION ALL SELECT 'cn_observed_event', count() FROM markorbit_facts.cn_observed_event FINAL
    UNION ALL SELECT 'cn_case_relation_current', count() FROM markorbit_facts.cn_case_relation_current FINAL WHERE is_deleted = 0
    UNION ALL SELECT 'cn_scope_carve_out_current', count() FROM markorbit_facts.cn_scope_carve_out_current FINAL WHERE is_deleted = 0
    UNION ALL SELECT 'cn_priority_current', count() FROM markorbit_facts.cn_priority_current FINAL WHERE is_deleted = 0
    UNION ALL SELECT 'cn_madrid_current', count() FROM markorbit_facts.cn_madrid_current FINAL WHERE is_deleted = 0
)
ORDER BY table_name
"@

Run-ClickHouseQuery "03_case_quality.csv" @"
SELECT
    count() AS cases,
    countIf(application_number = '') AS empty_application_number,
    countIf(case_family_root = '') AS empty_family_root,
    countIf(filing_date IS NULL) AS null_filing_date,
    countIf(mark_name_raw = '') AS empty_mark_name,
    countIf(length(classes) = 0) AS empty_classes,
    countIf(length(data_quality_flags) > 0) AS cases_with_quality_flags,
    countIf(is_derived_case = 1) AS derived_cases,
    countIf(filing_route = 'MADRID_DESIGNATION_CN') AS madrid_designation_cn_cases
FROM markorbit_facts.cn_case_current FINAL
WHERE is_deleted = 0
"@

Run-ClickHouseQuery "04_goods_quality.csv" @"
SELECT
    count() AS case_class_scopes,
    sum(source_item_count) AS source_items,
    sum(interpreted_active_item_count) AS interpreted_active_items,
    sum(interpreted_inactive_item_count) AS interpreted_inactive_items,
    sum(unmapped_status_item_count) AS unmapped_status_items,
    countIf(interpretation_complete = 1) AS complete_scopes,
    countIf(interpretation_complete = 0) AS incomplete_scopes,
    max(source_item_count) AS max_items_one_scope
FROM markorbit_facts.cn_case_scope_current FINAL
WHERE is_deleted = 0
"@

Run-ClickHouseQuery "05_goods_status_codes.csv" @"
SELECT arrayJoin(observed_status_codes) AS raw_status_code, count() AS scope_count,
       sum(source_item_count) AS source_items_in_scopes
FROM markorbit_facts.cn_case_scope_current FINAL
WHERE is_deleted = 0
GROUP BY raw_status_code
ORDER BY scope_count DESC
"@

Run-ClickHouseQuery "06_party_quality.csv" @"
SELECT role, count() AS current_relations, uniqExact(application_number) AS cases,
       countIf(entity_id IS NULL) AS unresolved_entity,
       countIf(raw_name = '') AS empty_name,
       countIf(raw_address = '') AS empty_address,
       countIf(country_code = '') AS empty_country
FROM markorbit_facts.cn_case_party_current FINAL
WHERE is_deleted = 0 AND is_current = 1
GROUP BY role ORDER BY role
"@

Run-ClickHouseQuery "07_event_types.csv" @"
SELECT event_type, affected_scope, field_name, evidence_level, legal_effect,
       count() AS event_count, countIf(source_file = '') AS empty_source_file,
       countIf(source_row = 0) AS zero_source_row
FROM markorbit_facts.cn_observed_event FINAL
GROUP BY event_type, affected_scope, field_name, evidence_level, legal_effect
ORDER BY event_count DESC
"@

Run-ClickHouseQuery "08_derived_cases.jsonl" @"
SELECT application_number, case_family_root, suffix_path, filing_route,
       international_registration_number, classes, filing_date,
       source_file, source_start_line
FROM markorbit_facts.cn_case_current FINAL
WHERE is_deleted = 0 AND is_derived_case = 1
ORDER BY application_number LIMIT 200
"@ "JSONEachRow"

Run-ClickHouseQuery "09_case_relations.jsonl" @"
SELECT * FROM markorbit_facts.cn_case_relation_current FINAL
WHERE is_deleted = 0
ORDER BY source_application_number, target_application_number LIMIT 200
"@ "JSONEachRow"

Run-ClickHouseQuery "10_case_samples.jsonl" @"
SELECT * FROM markorbit_facts.cn_case_current FINAL
WHERE is_deleted = 0 ORDER BY application_number LIMIT 100
"@ "JSONEachRow"

Run-ClickHouseQuery "11_scope_samples.jsonl" @"
SELECT * FROM markorbit_facts.cn_case_scope_current FINAL
WHERE is_deleted = 0 ORDER BY application_number, class_no LIMIT 100
"@ "JSONEachRow"

Run-ClickHouseQuery "12_party_samples.jsonl" @"
SELECT * FROM markorbit_facts.cn_case_party_current FINAL
WHERE is_deleted = 0 AND is_current = 1
ORDER BY application_number, role LIMIT 100
"@ "JSONEachRow"

Run-ClickHouseQuery "13_event_samples.jsonl" @"
SELECT * FROM markorbit_facts.cn_observed_event FINAL
ORDER BY application_number, event_date, event_type LIMIT 200
"@ "JSONEachRow"

Run-ClickHouseQuery "14_date_ranges.csv" @"
SELECT min(filing_date) AS min_filing_date, max(filing_date) AS max_filing_date,
       min(prelim_pub_date) AS min_prelim_pub_date, max(prelim_pub_date) AS max_prelim_pub_date,
       min(registration_pub_date) AS min_registration_pub_date, max(registration_pub_date) AS max_registration_pub_date,
       min(valid_until) AS min_valid_until, max(valid_until) AS max_valid_until
FROM markorbit_facts.cn_case_current FINAL WHERE is_deleted = 0
"@

Run-PostgresQuery "20_postgres_columns.csv" @"
SELECT table_schema, table_name, ordinal_position, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema IN ('control', 'entity')
ORDER BY table_schema, table_name, ordinal_position;
"@

Run-PostgresQuery "21_source_packages.csv" @"
SELECT package_id, package_sequence, file_name, package_kind, partition_dimension,
       partition_value, source_period_start, source_period_end, source_sequence,
       source_rank, status, file_size, sha256, processed_at, archived_path, error_message
FROM control.source_package ORDER BY source_rank, package_sequence;
"@

Run-PostgresQuery "22_package_files.csv" @"
SELECT p.file_name AS package_name, f.internal_name, f.file_role, f.content_encoding,
       f.physical_rows, f.logical_rows, f.continuation_rows, f.repaired_rows,
       f.failed_rows, f.replacement_chars, f.max_record_length, f.max_field_length
FROM control.source_package_file f
JOIN control.source_package p ON p.package_id = f.package_id
ORDER BY p.source_rank, p.file_name, f.file_role;
"@

Run-PostgresQuery "23_entity_quality.csv" @"
SELECT m.role, count(*) AS mentions, count(*) FILTER (WHERE m.entity_id IS NULL) AS unresolved,
       count(DISTINCT m.entity_id) FILTER (WHERE m.entity_id IS NOT NULL) AS resolved_entities,
       count(*) FILTER (WHERE m.raw_address = '') AS empty_address
FROM entity.entity_mention m GROUP BY m.role ORDER BY m.role;
"@

Run-PostgresQuery "24_quality_issues.csv" @"
SELECT p.file_name, q.issue_type, q.severity, count(*) AS issue_records,
       sum(q.occurrence_count) AS occurrences
FROM control.data_quality_issue q
LEFT JOIN control.source_package p ON p.package_id = q.package_id
GROUP BY p.file_name, q.issue_type, q.severity
ORDER BY occurrences DESC;
"@

Run-PostgresQuery "25_goods_code_mapping.csv" @"
SELECT * FROM control.source_code_mapping
WHERE jurisdiction = 'CN' AND field_name = 'goods_status'
ORDER BY raw_code, mapping_version;
"@

$readme = @"
MarkOrbit CN field audit M1.5
Generated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Directory: $reportDir

Outputs are copied as raw UTF-8 bytes from the database containers.
"@
[System.IO.File]::WriteAllText((Join-Path $reportDir "00_README.txt"), $readme, $utf8NoBom)

$zipPath = "$reportDir.zip"
Compress-Archive -Path (Join-Path $reportDir "*") -DestinationPath $zipPath -Force
Remove-Item -Recurse -Force $tempDir
Write-Host "Audit completed: $reportDir" -ForegroundColor Green
Write-Host "ZIP: $zipPath" -ForegroundColor Green
