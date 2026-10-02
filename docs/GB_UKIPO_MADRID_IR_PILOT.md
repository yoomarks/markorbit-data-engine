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
floor; Apply rechecks live E capacity. Preflight, freeze, and Apply also bind the
DSN and PostgreSQL cluster identity to the single running Compose PostgreSQL
container and require its data directory to be a writable bind mount under
`E:\MarkOrbitData` created with the external-storage Compose topology. A Docker
managed volume or any D/F-backed mount fails closed.

Apply rechecks the exact ordered first-1,000-row digest immediately before the
insert. The same database transaction stores the frozen plan, execution identity,
E topology, and exact receipt payload. Receipt publication is immutable and
atomic; if filesystem publication fails after commit, an exact retry validates
the committed rows and reconstructs only the matching receipt. No full Madrid import, Domestic resume,
journal ingestion, current-state assertion, serving cutover, ClickHouse change,
or source cleanup is authorized.

After independent merge, the pilot plan must be frozen from clean latest main.
Exact Apply authority will be:

```text
GO #855 GB-MADRID-IR-PILOT <plan-sha> FIRST-1000-ONLY
```

After Apply, a separate read-only source-to-database audit and a newly frozen
full-resume plan are required for rows 1,001 through 109,695.
