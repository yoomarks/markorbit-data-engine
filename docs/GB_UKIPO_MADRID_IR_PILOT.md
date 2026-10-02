# GB UKIPO Madrid-IR historical pilot (#855)

The Madrid-IR 2018 stock contains 109,695 physical source rows: 109,687
accepted rows and eight explicit malformed-width quarantines. It is historical
source evidence, not current UKIPO registry truth. All physical co-owner rows,
source ordinals, raw status values, classes, and lineage remain preserved.

`app.global_trademarks.gb_madrid_ir_pilot` admits only the first 1,000 ordered
physical rows from the independently accepted E structured stage. It reuses the
existing co-owner-safe `historical_source_row_v2` schema and row decoder. The
plan binds the E relocation plan/receipt/manifest/independent audit and official
F raw evidence through the accepted E-stage reader. It also binds the exact
PostgreSQL system identifier, loopback endpoint, container/image identity and
the existing external-storage Compose guard. The live PostgreSQL data mount
must be the single writable `/var/lib/postgresql/data` bind under
`E:\MarkOrbitData`; the database name alone is not placement evidence.

Preflight requires an empty Madrid-IR run and zero Madrid-IR rows in the
production `markorbit` database. Freeze and Apply require a clean checkout whose
HEAD exactly equals live `origin/main`. The plan records only the E reserve
floor; Apply rechecks live E capacity. Preflight, freeze, and Apply also bind the
DSN and PostgreSQL cluster identity to the single running Compose PostgreSQL
container and require its data directory to be a writable bind mount under
`E:\MarkOrbitData` created with the external-storage Compose topology. A Docker
managed volume or any D/F-backed mount fails closed.

Apply materializes the exact 1,000 rows once and recomputes their ordered
identity immediately before insert. The same transaction persists the source
rows, exact plan, execution topology, and canonical receipt payload.
Reconciliation rereads all 1,000 committed row identities in ordinal order and
requires their digest to match the frozen plan. Receipt and plan files use
same-directory atomic publication plus byte/SHA-256 verification; an exact
committed database receipt can repair a partial post-commit file, while a file
without matching database evidence still fails closed. An ambiguous commit is
reconciled on a new connection, and exact retries never INSERT again. No full
Madrid import, Domestic resume, journal ingestion, current-state assertion,
serving cutover, ClickHouse change, or source cleanup is authorized.

After independent merge, the pilot plan must be frozen from clean latest main.
Exact Apply authority will be:

```text
GO #855 GB-MADRID-IR-PILOT <plan-sha> FIRST-1000-ONLY
```

After Apply, a separate read-only source-to-database audit and a newly frozen
full-resume plan are required for rows 1,001 through 109,695.
