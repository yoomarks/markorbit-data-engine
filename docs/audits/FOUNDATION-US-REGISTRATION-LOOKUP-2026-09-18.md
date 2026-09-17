# Foundation US registration lookup — 2026-09-18

## Outcome

The exact US registration-number path is implemented as a bounded two-stage serving read:

1. `us_registration_candidate_lookup` resolves at most 500 serial candidates through `(registration_number, serial_number)` ordering.
2. The candidate serials are verified against `us_case_current FINAL` through its `serial_number` primary key.

The candidate table intentionally tolerates historical mappings. A registration-number correction can leave an old candidate, but it cannot produce a stale API result because the second stage requires the current case to retain the requested registration number.

The authenticated additive route is `GET /api/v1/us/registrations/{registration_number}`. It returns observed current case facts, `not_found`, an explicit scope error, or service unavailable. It never presents provider data as a legal-status conclusion.

## Local non-production evidence

The migration and a 155,000-row current-case backfill were applied only to the local Docker development ClickHouse at port 8123. Seven complete two-stage reads for registration `0000001` returned one current match:

- p50: 10.399 ms
- p95: 13.324 ms
- maximum observed sample: 14.028 ms
- query cache disabled by the frozen read budget
- candidate ceiling: 500
- max rows to read per statement: 1,000,000
- max bytes to read per statement: 256 MiB
- max execution time per statement: 3 seconds

The lookup plan pruned the candidate table to 1/19 granules. The verification stage used a one-element `serial_number` primary-key set; it did not scan the current-case corpus.

This is functional evidence, not production-scale SLO acceptance.

## Production boundary

Read-only preflight identified the accepted-US target at port 28123 as production storage (`/var/lib/markorbit-clickhouse-production/` and `hot_us`). No schema or data mutation was executed there.

The production capability therefore remains `IMPLEMENTED_REQUIRES_PRODUCTION_BACKFILL`. Runtime checks require the schema-version marker `US_REGISTRATION_CANDIDATE_LOOKUP_READY_V1`; without it, the endpoint fails closed with `DATA_ENGINE_REGISTRATION_LOOKUP_UNAVAILABLE` before reading the projection.

Production activation requires separately authorized schema creation, a frozen-epoch backfill of current US cases, completeness validation, the ready marker, and a seven-run production benchmark before the capability can advance to supported.
