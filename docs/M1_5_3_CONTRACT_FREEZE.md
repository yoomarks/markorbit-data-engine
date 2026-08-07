# M1.5.3 Contract Freeze

M1.5.3 stops the lineage hotfix chain and freezes one boundary:

- stage tables: `source_file`, `source_start_line`, `source_end_line`, `row_hash`
- aggregate/current/history/event tables: `source_file`, `source_first_line`, `source_last_line`, `source_row_hash`

## ClickHouse 24.8 rule

Aggregation inputs use private SQL names (`stage_source_start_line`, `stage_source_end_line`).
Permanent output aliases are never reused as aggregate inputs in the same query block.
Party role touch aggregation uses `touched_*` output aliases and is defined once.

## Fast validation gate

Before importing any real ZIP:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-cn-contract.ps1
```

The gate checks runtime schemas, the G-number model, and executes the complete `_publish`
SQL path against a random package with zero stage rows. That compiles every production
publish statement without reparsing `1999.zip`.

Only after this gate passes should `retry-cn.ps1` be used.
