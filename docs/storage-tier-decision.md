# Storage tier decision gate

`python -m app.storage_tier_decision` is the fail-closed decision layer between the read-only storage evidence and large-country scale-out.

It does not migrate data, mutate tables, compact the live corpus, run `OPTIMIZE FINAL`, or revalidate accepted source packages.

## Current CN decision

The current serving contract requires these tables to remain Hot unless a versioned, equivalence-tested serving replacement exists:

- `cn_goods_item_current`
- `cn_case_party_current`
- the API-serving portion of `cn_observed_event`

The following are narrower Warm/compaction candidates, subject to their explicit constraints:

- `cn_goods_item_observation`: baseline/history may move Warm only after the current summary-count dependency is replaced or preserved with equivalence evidence.
- `cn_observed_event`: only verified reconstructible baseline subsets may compact or move Warm; true deltas, prior-state evidence, and current API-serving events remain durable.
- legacy wide party relation history: Warm candidate only after recovery/consumer verification; static absence of a consumer is not sufficient evidence for deletion.

`cn_goods_item_current` is not a Warm candidate merely because it is the largest table. The current case API exposes the table through a dynamic `SELECT *` contract, so a narrow Hot projection requires an explicit API contract/versioning migration first.

## Placement states

The decision gate accepts optional target-host placement evidence:

- no evidence: `WAITING_TARGET_HOST_READINESS`
- read-only readiness with `safe_to_cutover=true`: `READY_FOR_CONTROLLED_CUTOVER`
- successful cutover evidence: `PASS` only when Hot/Cold activation, source-volume retention, rollback availability, Cold disk registration, no source-package revalidation, and exact `system.parts` metadata equivalence are all proven

A green readiness report is permission to consider the controlled cutover; it is not proof that storage placement has completed.

## Scale-out meaning

`storage_scale_out.status=GO` means only that the storage-architecture blocker has been removed. It does not bypass jurisdiction-specific credentials, acquisition readiness, acceptance gates, or rollout approvals.

Until the placement gate is `PASS`, the storage scale-out result remains `NO_GO`, even when the Hot/Warm representation contract itself is valid.

## Examples

Build a live capacity profile and static consumer inventory, but deliberately leave placement unresolved:

```powershell
python -m app.storage_tier_decision --compact
```

Evaluate a saved target-host readiness/cutover receipt:

```powershell
python -m app.storage_tier_decision `
  --capacity-json .\reports\storage-capacity.json `
  --inventory-json .\reports\storage-consumers.json `
  --placement-json .\reports\clickhouse-cutover.json
```

For an operator/CI gate that must fail until storage scale-out is actually allowed:

```powershell
python -m app.storage_tier_decision `
  --capacity-json .\reports\storage-capacity.json `
  --inventory-json .\reports\storage-consumers.json `
  --placement-json .\reports\clickhouse-cutover.json `
  --require-storage-scale-out-go
```

Exit code `5` means the storage scale-out gate is still closed. Exit code `4` means the representation/consumer contract itself drifted and must be repaired before any scale-out or demotion decision.
