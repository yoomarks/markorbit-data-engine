# Foundation bounded Assignment and TTAB reads — 2026-09-18

## Decision

Assignment and TTAB serial lookups do not require a new latest-state table. The verified root cause was query order: both APIs used a serial-keyed property table, then rebuilt latest parent state across the entire corpus.

The repaired path is fixed and bounded:

1. Resolve parent identities and observed package IDs through the property table's leading `serial_number` key.
2. Fail closed if the serial resolves to more than 500 parent identities.
3. Read latest parent state only for those literal identities through the parent table's leading key.
4. Keep a relationship only when the serial-bearing property was observed in that parent's latest package. This preserves removal/correction semantics from the append-only history.

No production data, schema, index, table, or deployment state was changed.

## Accepted-target evidence

Target: accepted US ClickHouse corpus on `127.0.0.1:28123`. Each statement ran seven times with query cache disabled, one thread, a three-second limit, one million rows, 256 MiB read bytes, and 201 result rows.

| Capability stage | Sample | p50 | p95 | Max read rows | Max read bytes | Primary-key evidence |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Assignment candidate resolve | serial `71614627` | 45.845 ms | 52.254 ms | 16,384 | 412,733 | 2/971 property granules |
| Assignment latest parents | 8 reel/frame IDs | 49.612 ms | 50.097 ms | 114,688 | 2,964,519 | 14/200 record granules |
| TTAB candidate resolve | serial `79412016` | 45.802 ms | 60.007 ms | 32,768 | 954,234 | 4/277 property granules |
| TTAB latest parents | 1 proceeding ID | 45.660 ms | 50.904 ms | 8,192 | 271,413 | 1/98 proceeding granules |

Every stage passed the 400 ms p95 target. No stage used `FINAL`, `OFFSET`, exact `count(*)`, a query-cache hit, or a corpus-wide latest-state aggregation.

The Phase 2 before-change query exceeded the one-million-row ceiling while aggregating 1.62 million Assignment parent rows and 1.46 million TTAB parent rows. The capability registry is therefore advanced from `UNSUPPORTED_REQUIRES_LATEST_STATE_READ_MODEL` to `SUPPORTED_BOUNDED_TWO_STAGE` for these two lookup shapes only.

## Failure behavior

- Invalid serials retain their existing validation behavior.
- More than 500 candidate parents returns an explicit scope-exceeded error; the API does not silently truncate.
- A historical relationship removed by the latest parent package is excluded.
- An empty candidate set returns an empty result without reading parent history.
- Assignment remains recorded-fact evidence, not a legal-title conclusion.
- TTAB remains procedural evidence, not a legal-outcome or substantive-rights conclusion.
