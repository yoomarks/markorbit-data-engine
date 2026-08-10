# US Application Real-Data Sample Acceptance — 2026-08

## Status

**PASS — sample stage complete.**

This acceptance closes the sample-first validation stage for the U.S. Trademark Application ingestion path. It does **not** authorize full-corpus replay until the annual historical source set is independently proven complete with an explicit expected part count.

Accepted code baseline:

- `43d70f0a4121e3eb1c94001d63b853039e261ef6`
- schema: `US_M1.4`

## Real USPTO sources exercised

Ordered sample replay:

1. `apc18840407-20251231-06.zip` — historical Applications sample
2. `apc260109.zip` — daily Applications sample
3. `apc260109.zip` — controlled same-package retry

Raw files remained compressed ZIP sources and were streamed without persistent XML extraction.

## Historical → daily acceptance

Historical sample:

- package kind: `HISTORICAL_APPLICATIONS`
- partition: `1884-04-07/2025-12-31#006`
- source rank: `1020251231006078`
- case count: `155000`

Daily sample:

- package kind: `DAILY_APPLICATIONS`
- partition: `2026-01-09`
- source rank: `3020260109000091`
- case count: `26834`

The ordered replay preserved transaction-date freshness across historical and daily packages. Final stale-current violations were `0`.

Real overlap validation previously exercised both directions:

- cases where daily transaction date was newer than historical: daily remained current;
- a case where historical transaction date was newer than the later daily package: historical remained current.

This proves the two separate ordering rules used by the Application pipeline:

1. package replay order is monotonic `source_rank`;
2. within that replay order, case-current freshness is determined by `transaction_date`.

## Madrid filing current semantics

The daily source contained `1099` raw Madrid filing status records. Real-data preflight compacted them to `1056` current Madrid filing identities.

The current snapshot preserves one deterministic current filing state per Madrid filing identity while raw source facts, case observations, and Madrid event history remain preserved separately.

Real current-winner checks passed for:

- `78650787 / A0003329` → status date `2022-12-14`, entry `13`;
- `78778400 / A0036168` → status date `2020-09-17`, entry `40`;
- `97608522 / A0127955` → status date `2024-07-15`, entry `350`;
- `98509826 / A0151027` → status date `2025-12-16`, entry `536`.

## Same-package retry / idempotency

A controlled retry of `apc260109.zip` completed successfully with:

- retry case count: `26834`;
- retry Madrid current rows: `1056`;
- registry status after retry: `SUCCESS`;
- stale current before retry: `0`;
- stale current after retry: `0`;
- semantic current snapshot differences: none;
- daily event / Madrid event / case observation differences: none.

The retry cleanup path therefore reproduces the same semantic Application state for this real package.

## Proven invariants

The real-data sample stage now proves:

- compressed ZIP streaming works for official Application XML;
- historical → daily ordered replay works;
- out-of-order package insertion is blocked;
- case-current freshness uses transaction date rather than package rank alone;
- current child snapshots reconcile without corrupting append-only history;
- Madrid filing current-state compaction is deterministic on real source data;
- same-package cleanup + replay is semantically idempotent;
- durable case observations survive as the chronology source;
- no stale case-current rows remain after ordered replay or retry.

## Next gate: full annual source completeness

Full-corpus replay remains blocked until source completeness passes.

The next stage must use `app.us.source_preflight` / `scripts/preflight-us-source-replay.ps1` and preserve the existing strict rule:

- historical numbering starts at `01`;
- parts must be continuous;
- the total part count must be explicitly pinned from an authoritative source manifest;
- the expected total must never be inferred from the highest locally observed suffix;
- missing leading, interior, or tail parts keep replay `NOT_READY`;
- daily continuation may be evaluated only after the historical baseline is complete.

Assignment and TTAB remain outside this stage until the Application full-source replay gate passes.
