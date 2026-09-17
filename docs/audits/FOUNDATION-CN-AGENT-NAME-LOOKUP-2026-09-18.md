# Foundation CN Agent Name Lookup — 2026-09-18

## Verified problem

The accepted production baseline contains 90,960 rows in
`markorbit_facts.cn_agent_current`. Its sort key is `agent_code`; the Phase 2 exact-name benchmark
read all 90,960 rows and all 14 granules. The observed p95 was 64.75 ms only because the current
table is small, so the query remained unsupported as a scale-independent API.

## Bounded read model

`CN_AGENT_NAME_LOOKUP_V1` adds an exact `agent_name_norm` candidate projection ordered by
`(normalized_name, agent_code)`. Future official Agent-current inserts feed the projection through
a materialized view. A separately governed backfill is required for pre-existing rows.

The runtime resolves at most 500 candidate Agent codes, then revalidates those codes against
`cn_agent_current FINAL`. The second bounded stage prevents changed or cleared names from being
returned through an old projection key. More than 500 candidates fails closed. No fuzzy/prefix
search, legal identity verification, or represented-relationship conclusion is created.

## Non-production evidence

The local development projection was backfilled from 59,204 current Agent rows. For a normalized
name with two current Agent-code facts, `EXPLAIN indexes = 1` selected the `normalized_name`
primary key and 1/8 local granules. Seven complete readiness + candidate + current-fact reads
measured p50 147.52 ms, p95 155.53 ms, and max 155.53 ms with two results. This is below the
frozen 300 ms entity-name candidate SLO on the local validation host; it is not production-scale
latency evidence.

## Production boundary

The capability state is `IMPLEMENTED_REQUIRES_PRODUCTION_BACKFILL`. Runtime requires
`CN_AGENT_NAME_LOOKUP_READY_V1` and fails closed before reading candidates until a separately
authorized production backfill, completeness review, READY marker, and production benchmark are
complete. This change performs no production ClickHouse mutation.
