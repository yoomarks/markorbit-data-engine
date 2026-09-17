# Foundation US Attorney Name Lookup — 2026-09-18

## Verified problem

The accepted production baseline contains 15,505,671 rows in
`markorbit_facts.us_correspondent_current` (3.70 GiB). Its sort key is
`(serial_number, correspondent_key)`, so an exact `attorney_name` predicate is not
scale-independent: the Phase 2 plan selected all 1,950 granules.

## Bounded read model

`US_ATTORNEY_NAME_LOOKUP_V1` adds an exact normalized-name candidate projection ordered by
`(normalized_name, serial_number, correspondent_key)`. Future correspondent inserts feed the
projection through a materialized view. A separately governed backfill is still required for
pre-existing rows.

The runtime performs two bounded stages:

1. resolve at most 500 candidate correspondent records by normalized attorney name;
2. revalidate those candidate serials against `us_correspondent_current FINAL` and return only
   rows whose current attorney name still matches.

The second stage is required because a correspondent's attorney name can change or become empty.
It prevents an old projection key from being reported as a current fact. More than 500 candidates
fails closed; fuzzy, prefix, ranked, and identity-resolution semantics are not implied.

## Non-production evidence

The local development ClickHouse projection was backfilled from 11 non-empty attorney rows across
11 serials. `EXPLAIN indexes = 1` selected the `normalized_name` primary key (1/1 local granule).
Seven complete readiness + candidate + current-fact reads for `Bailey Blaies` measured:

- p50: 138.51 ms
- p95: 145.61 ms
- max: 145.61 ms
- result count: 1 current fact

This is below the frozen 300 ms entity-name candidate SLO on the local validation host. It is not
production-scale latency evidence.

## Production boundary

The capability state is `IMPLEMENTED_REQUIRES_PRODUCTION_BACKFILL`. Runtime requires
`US_ATTORNEY_NAME_LOOKUP_READY_V1` and fails closed before reading candidates until a separately
authorized production backfill, completeness review, READY marker, and production benchmark are
complete. This change performs no production ClickHouse mutation.
