# CN goods Hot footprint audit

`cn_goods_item_current` is currently a direct Hot serving dependency. The CN case endpoint selects `*` from this table and serializes ClickHouse column names into the response, so every deployed table column is part of the present response surface in practice.

This means a narrower replacement table is **not** a compatibility-preserving optimization today. Removing a physical column would also remove a response field unless the serving/API contract is changed deliberately.

## What the audit measures

`app.storage_goods_hot_footprint` reads only ClickHouse metadata:

- `system.columns` for deployed column names/types/key membership and compressed, uncompressed, and marks bytes;
- `system.tables` for engine, sorting key, primary key, and partition key;
- the checked-out `app/main_core.py` to confirm the current `SELECT *` serving contract.

It does not scan `cn_goods_item_current` rows, use `FINAL`, mutate data, run `OPTIMIZE`, or authorize a migration.

The resulting per-column compressed-byte shares show where the ~Hot footprint is physically concentrated without repeating the expensive CN corpus validation.

## Decision boundary

While the current case API exposes all table columns:

- compatibility-preserving removable bytes are zero;
- no in-place column-removal/narrow-table cutover is allowed;
- `migration_authorized` remains false even when the audit status is PASS.

A PASS means only that deployed metadata is visible and the expected current API contract is confirmed.

## Safe next optimization classes

After collecting the metadata report, evaluate in this order:

1. **Codec/type/order-key efficiency** — determine whether dominant columns can be represented more efficiently without changing their logical values or response fields.
2. **Compatibility projection/view** — only if every currently exposed response field remains available and query performance/storage behavior is proven.
3. **Explicit API versioning** — required before intentionally dropping response fields or changing their semantics.
4. **Physical cutover** — only after rebuild/rollback evidence, capacity headroom, and an explicit operator-approved migration plan.

No option above permits destructive changes merely because the official raw source can rebuild the table.

## Operator command

On the Windows host, after updating to a commit containing this audit:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/audit-cn-goods-hot-footprint.ps1
```

The command starts no persistent service. It requires the existing ClickHouse service to be running, launches only a disposable no-deps worker with the checked-out `app` mounted read-only, and writes a JSON report under `reports/`.
