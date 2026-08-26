# Conservative CN storage reclaim projection

`python -m app.storage_reclaim_projection` converts already-collected capacity and deep Storage V2 audit evidence into planning-only CN reclaim estimates.

The projection is read-only. It does not compact, mutate, move, delete, or `OPTIMIZE` any table and does not authorize those actions.

## Why the estimate is deliberately limited

ClickHouse compressed bytes are not uniform per row. The projection therefore reports a row-share-based **planning estimate**, not measured reclaimable bytes:

`candidate row share × active compressed table bytes`

Every candidate also carries a zero lower bound, full-table upper bound, and an explicit warning that realized savings require a reversible fixture or approved before/after measurement.

The candidate sets are limited to Storage V2 evidence already identified as reconstructible/no-op history:

- `cn_goods_item_observation`: `FIRST_OBSERVED` + `REOBSERVED` baseline/history rows;
- `cn_observed_event`: only the deep audit's verified reconstructible baseline candidate rows;
- `cn_case_party_relation_history`: unchanged `OBSERVED_CURRENT` legacy wide-history rows.

Current serving anchors such as `cn_goods_item_current` and `cn_case_party_current` are never included as reclaim candidates. `cn_observed_event` remains a protected serving table even though a verified subset inside it can be a compaction candidate.

## No implicit heavy scan

Without a deep-audit receipt the command returns `WAITING_DEEP_AUDIT_EVIDENCE`. It does **not** silently scan the large history/event tables.

Use a saved receipt when available:

```powershell
python -m app.storage_reclaim_projection `
  --capacity-json .\reports\storage-capacity.json `
  --deep-audit-json .\reports\storage-v2-deep-audit.json `
  --compact
```

A live deep audit is possible only with explicit opt-in:

```powershell
python -m app.storage_reclaim_projection --live-deep-audit --compact
```

That option performs read-only aggregate scans over the CN observation/event/party-history tables. It should be scheduled deliberately on the target host; it is not part of Hot/Cold cutover readiness and is not required to migrate the existing authoritative ClickHouse volume to E: Hot.

## US and global sizing

The report intentionally emits no numeric US or global projection from CN multipliers. Different jurisdictions can have materially different source history, goods/event cardinality, schemas, and compression. US/global numeric budgets require their own accepted corpus/profile evidence.

This is a design constraint: a CN storage ratio is not valid evidence for estimating a full US or global corpus.
