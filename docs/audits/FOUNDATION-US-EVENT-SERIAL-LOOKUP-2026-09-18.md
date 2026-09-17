# Foundation US serial event lookup — 2026-09-18

## Outcome

The US official-event timeline now has a serial-keyed serving projection and an authenticated bounded route:

- projection: `us_event_serial_history`, ordered by `(serial_number, event_key)`;
- route: `GET /api/v1/us/cases/{serial_number}/events`;
- ceiling: 5,000 events per request under the frozen 3-second, 1,000,000-row, 256 MiB budget;
- semantics: official USPTO event facts, not a legal-status conclusion.

This is deliberately described as a trademark event timeline. It does not claim that the generic temporal relationship-edge timeline is implemented.

## Local non-production evidence

The schema and a 513-row event backfill covering 498 serials were applied only to the local Docker development ClickHouse at port 8123. Seven complete readiness-plus-event reads for the highest-cardinality local serial (`60157469`, six events) produced:

- p50: 4.822 ms;
- p95: 6.879 ms;
- maximum observed sample: 7.664 ms;
- query cache disabled by the frozen read budget.

`EXPLAIN indexes = 1` selected the `serial_number` primary-key condition and one local granule. This is functional plan evidence, not production-scale SLO acceptance.

## Production boundary

The accepted US production corpus contains 300,970,551 source event rows (45.62 GiB) whose source table is ordered by `event_key`. No production schema, backfill, or ready-marker mutation was executed.

The capability remains `IMPLEMENTED_REQUIRES_PRODUCTION_BACKFILL`. Runtime requires `US_EVENT_SERIAL_LOOKUP_READY_V1` and fails closed before projection access until a separately authorized frozen-epoch backfill, completeness review, ready marker, and seven-run production benchmark are complete.
