# GB UKIPO Madrid-IR historical pilot (#855)

The Madrid-IR 2018 stock contains 109,695 physical source rows: 109,687
accepted rows and eight explicit malformed-width quarantines. It is historical
source evidence, not current UKIPO registry truth. All physical co-owner rows,
source ordinals, raw status values, classes, and lineage remain preserved.

`app.global_trademarks.gb_madrid_ir_pilot` admits only the first 1,000 ordered
physical rows from the independently accepted E structured stage. It reuses the
existing co-owner-safe `historical_source_row_v2` schema and row decoder. The
plan binds the E relocation plan/receipt/manifest/independent audit and official
F raw evidence through the accepted E-stage reader.

Preflight requires an empty Madrid-IR run and zero Madrid-IR rows in the
production `markorbit` database. Freeze and Apply require a clean checkout whose
HEAD exactly equals live `origin/main`. The plan records only the E reserve
floor; Apply rechecks live E capacity. No full Madrid import, Domestic resume,
journal ingestion, current-state assertion, serving cutover, ClickHouse change,
or source cleanup is authorized.

After independent merge, the pilot plan must be frozen from clean latest main.
Exact Apply authority will be:

```text
GO #855 GB-MADRID-IR-PILOT <plan-sha> FIRST-1000-ONLY
```

After Apply, a separate read-only source-to-database audit and a newly frozen
full-resume plan are required for rows 1,001 through 109,695.
