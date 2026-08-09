# M1.6 CN Goods Lifecycle & Delta Merge

Status: VALIDATED ON REAL BASE + MONTHLY DATA; RELEASE GATES COMPLETE

## Why M1.6 exists

The CN goods status field is an item-level signal. Codes `0`, `1`, and `2` must not be collapsed into trademark/case status or legal cause. Monthly packages are deltas: omission from a monthly package is not deletion.

M1.5 aggregated each package directly into `cn_case_scope_current`. That is safe for full/base partitions but unsafe for monthly goods patches because a patch containing only changed goods could otherwise replace a class that contains additional unchanged goods.

## Frozen M1.6 invariants

1. Base data establishes the known goods item universe.
2. Monthly data updates only items explicitly present in that package.
3. Omitted goods remain current.
4. Touched class scopes are rebuilt from the complete durable item store.
5. Goods state, goods-scope state, trademark/case state, and legal cause remain separate layers.
6. `0/1/2` are preserved raw forever.
7. Code `0` means reversible/unresolved risk at the goods-item layer.
8. Code `1` means inactive high confidence but source-not-finalized at the goods-item layer.
9. Code `2` means final inactive at the goods-item layer.
10. None of `0/1/2` identifies legal cause.
11. Goods sequence alone is not unique and MUST NOT define identity.
12. Production and audit paths MUST use the same strict identity and record-boundary semantics.
13. A killed process must never strand a package permanently in `PROCESSING`.
14. Interrupted packages are cleaned and replayed from the authoritative ZIP; checkpoint/resume is not part of the production M1.6 runtime.

## Durable tables

- `cn_goods_item_current`: current state of every known goods/service item.
- `cn_goods_item_observation`: durable item observations and transitions.
- `cn_goods_scope_lifecycle_current`: class-level lifecycle counters derived from the complete durable item set.

`cn_case_scope_current` remains the compatibility/current search scope, but M1.6 reconstructs touched scopes from `cn_goods_item_current`, not from the current package alone.

## Goods item identity

M1.6 uses `CN_GOODS_ITEM_ID_V2_STRICT_SOURCE_FIELDS`:

`application_number + class_no + goods_sequence + similar_group + normalized goods_name`

Status is deliberately excluded from identity so a later status observation updates the same strict item. Intra-package status variants are resolved by `CN_GOODS_STATUS_RESOLUTION_V1_STRONGEST_SIGNAL`:

`2 > 1 > 0 > explicit inactive > unknown > explicit active > ordinary active/blank`

Source line is only a deterministic tie-breaker within equal semantic strength.

Real base data proved why the strict V2 key is required. Accepted 1999 evidence is `6,091,001` parsed/staged goods rows -> `6,090,916` logical items, with `85` exact-duplicate excess rows across `63` keys and `0` identity conflicts. The accepted 1999-2006 V2 audits all have `0` conflicting identity keys.

## Item status mapping

| raw | item semantic | source finality | operational effect |
|---|---|---|---|
| blank | `NO_NEGATIVE_SIGNAL` | `OPEN` | `EFFECTIVE_UNLESS_CONTRADICTED` |
| 0 | `REVERSIBLE_OR_UNRESOLVED_RISK` | `REVERSIBLE` | `EFFECTIVE_AT_RISK` |
| 1 | `INACTIVE_HIGH_CONFIDENCE` | `SOURCE_NOT_FINALIZED` | `INACTIVE_HIGH_CONFIDENCE` |
| 2 | `FINAL_INACTIVE` | `FINAL` | `INACTIVE_CONFIRMED` |

Mapping evidence is labelled `EMPIRICAL_DOMAIN_MAPPING`; it is intentionally not presented as an official CNIPA legal-cause dictionary.

## Replay and crash recovery

An M1.5 database can contain class-level scopes without durable goods-item history, so M1.6 requires a clean replay before accepting new data into such a database. Raw ZIPs remain authoritative.

CN ingestion holds a PostgreSQL session advisory lock across candidate selection -> ingest -> publish. If the process/container/host dies, PostgreSQL releases the lock automatically. On the next run, orphaned `PROCESSING` packages become `INTERRUPTED`, partial stage/published outputs are cleaned, and the package is replayed from the authoritative ZIP in `source_rank` order.

Manual ingestion uses a dedicated one-shot worker so the API remains online. Persistent worker execution is intentionally kept separate from controlled replay/audit workflows.

## CSV record-boundary compatibility

Real `2025_11.zip` exposed fully quoted CSV rows. Record-start detection therefore uses shared `csv.reader` semantics for quoted and unquoted prefixes. Production ingestion, identity audit, and raw-reader consumers now share the same boundary implementation; the earlier temporary M1.6 monkey patch was removed.

## Real-data acceptance evidence

### Base partitions 1999-2006

- all real packages replayed successfully under M1.6/V2
- all V2 identity audits completed with `0` conflicting identity keys
- `2004.zip` confirmed the ClickHouse spill/memory-safety settings resolve the prior aggregation OOM
- M1.6 full integrity audit: `PASS_WITH_WARNINGS`, with `0` unexplained row loss, `0` parser failed rows, `0` final replacement-character rows, `0` final duplicates, `0` scope-without-case, `0` untraceable party orphans, and `0` unmapped goods status codes
- remaining warnings are documented source-quality/source-incompleteness warnings

### `2023_1.zip`

Identity audit PASS:
- parsed/staged: `4,063,348 / 4,063,348`
- logical strict items: `4,063,325`
- collapsed/exact duplicate excess rows: `23`
- status variants: `0`
- identity conflicts: `0`

Monthly acceptance PASS:
- incoming/current strict keys: `4,063,325 / 4,063,325`
- cross-package matches: `13,988`
- transitions: `4,049,337 FIRST_OBSERVED + 276 REOBSERVED + 13,712 STATUS_CHANGED`
- touched scopes: `360,958`, all lifecycle/case-scope mismatches `0`
- omitted durable items preserved: `3,097` across `447` scopes
- impossible item loss: `0`

### `2025_11.zip`

Identity audit PASS:
- parsed/staged: `5,788,907 / 5,788,907`
- logical strict items: `5,772,737`
- collapsed rows: `16,170`
- exact duplicate keys/excess rows: `16,166 / 16,170`
- status variants: `0`
- identity conflicts: `0`

Monthly->monthly acceptance PASS under `CN_M16_MONTHLY_PATCH_POLICY_V6_CHAINED_MONTHLY_LINEAGE`:
- incoming/current strict keys: `5,772,737 / 5,772,737`
- cross-package strict-key matches: `204`
- prior monthly origin matches: `204`
- transitions: `5,772,533 FIRST_OBSERVED + 204 STATUS_CHANGED`
- touched scopes: `545,833`, all lifecycle/case-scope mismatches `0`
- omitted durable items preserved: `97` across `14` scopes
- impossible item loss: `0`

This directly validates monthly omission != deletion and monthly->monthly first-source lineage preservation on real data.

## Validation workflow

1. `scripts/reset-m16.ps1`
2. `scripts/validate-cn-contract.ps1`
3. `scripts/validate-cn-fixture.ps1`
4. `scripts/validate-m16-goods.ps1`
5. replay base packages in source-rank order
6. run `scripts/audit-m16-goods-identity.ps1 <file>` for real packages
7. run `scripts/audit-m16-acceptance.ps1` for full integrity
8. ingest real monthly patches
9. run `scripts/audit-m16-monthly-patch.ps1 <file>` for durable lineage/omission reconciliation

## Case inference remains separate

M1.6 does not claim that a goods code proves why a trademark lost rights. A later case inference engine may combine filing date, preliminary publication, registration publication, validity dates, complete goods-scope changes, opposition/review/invalidation/cancellation decisions, and court judgments. Such output must be versioned, evidence-linked, confidence-scored, and clearly separated from official facts.
