# M1.6 CN Goods Lifecycle & Delta Merge

Status: IMPLEMENTED ON FEATURE BRANCH, RUNTIME VALIDATION REQUIRED

## Why M1.6 exists

The CN goods status field is an item-level signal. Codes `0`, `1`, and `2` must not be collapsed into trademark status or legal cause. Monthly packages are deltas: omission from a monthly package is not deletion.

M1.5 aggregated each package directly into `cn_case_scope_current`. That is safe for full/base partitions but unsafe for monthly goods patches because a patch containing only two changed goods could replace a class that originally contained many more goods.

## M1.6 invariants

1. Base data establishes the known goods item universe.
2. Monthly data updates only items explicitly present in that package.
3. Omitted goods remain current.
4. Scope is rebuilt from the complete durable item store after every touched package.
5. Goods state, goods-scope state, trademark/case state, and legal cause remain separate layers.
6. `0/1/2` are preserved raw forever.
7. Code `1` is an item-level high-confidence inactive signal, not a cause code.
8. Code `2` is an item-level final inactive signal, not a cause code.
9. Legal causes such as refusal, opposition, cancellation, non-use cancellation, invalidation, voluntary cancellation, or non-renewal require separate evidence or a separately versioned inference model.
10. A goods sequence value is not globally unique within one case/class and MUST NOT be used alone as item identity.
11. Production ingest SQL and validation/audit SQL MUST implement the same identity version; tests must exercise the runtime SQL builder used by `ingest_m16.py`.
12. A killed process must never strand a package permanently in `PROCESSING`.
13. Completed ZIP members may be reused only when both the PostgreSQL member checkpoint and retained ClickHouse stage rows are present.

## New durable tables

- `cn_goods_item_current`: current state of every known goods/service item.
- `cn_goods_item_observation`: append-oriented item observations and transitions.
- `cn_goods_scope_lifecycle_current`: class-level lifecycle counters derived from the complete item set.

The existing `cn_case_scope_current` remains the compatibility/current search scope, but M1.6 reconstructs touched scopes from `cn_goods_item_current` rather than from the current package alone.

## Goods item identity

The 1999 real-package audit proved that `(application_number, class_no, goods_sequence)` is not a valid unique item key. Sequence `0` is commonly reused for many different goods, and non-zero sequence values can also repeat across different similar groups.

M1.6 therefore uses identity version `CN_GOODS_ITEM_ID_V2_STRICT_SOURCE_FIELDS` with a deterministic source-observation key built from:

`application_number + class_no + goods_sequence + similar_group + normalized goods_name`

This deliberately favors preservation over aggressive merging. Exact repeated source rows may collapse to one logical item, but different goods names or similar groups must never be merged merely because their sequence values match.

The identity key does **not** include goods status, because a status change must update the same goods item rather than create a new identity.

A later cross-package reconciliation layer may introduce stronger identity linking if real monthly data proves that wording or similar-group values can change for the same legal goods item. Such linking must be evidence-based and must never silently overwrite the strict source observation identity.

The first V2 audit passed with zero conflicting keys, but a subsequent 1999 replay still produced the retired item count. This exposed a second implementation defect: `ingest_m16.py` routes production through `app/cn/goods_lifecycle_sql.py`, while the audit/fixture path had already been updated in `goods_lifecycle.py`. Runtime SQL was therefore still using the retired sequence-oriented key. The runtime builder is now also V2 and the contract test targets that exact builder so this split cannot silently recur.

For the 1999 package specifically, the V2 audit reports 6,091,001 staged rows and 82 exact duplicate excess rows with zero conflicting excess rows. Therefore the expected logical item count after a correct V2 production replay is 6,090,919. A replay that still produces 6,081,430 is evidence that the retired runtime identity is still in use.

## Item status mapping

| raw | item semantic | source finality | operational effect |
|---|---|---|---|
| blank | `NO_NEGATIVE_SIGNAL` | `OPEN` | `EFFECTIVE_UNLESS_CONTRADICTED` |
| 0 | `REVERSIBLE_OR_UNRESOLVED_RISK` | `REVERSIBLE` | `EFFECTIVE_AT_RISK` |
| 1 | `INACTIVE_HIGH_CONFIDENCE` | `SOURCE_NOT_FINALIZED` | `INACTIVE_HIGH_CONFIDENCE` |
| 2 | `FINAL_INACTIVE` | `FINAL` | `INACTIVE_CONFIRMED` |

Mapping evidence is labelled `EMPIRICAL_DOMAIN_MAPPING` and is intentionally not presented as an official CNIPA code dictionary.

## Replay boundary

An existing M1.5 database has class-level scopes but no durable item history because successful package staging rows were cleaned. M1.6 refuses to ingest new packages when `cn_case_scope_current` is populated but `cn_goods_item_current` is empty.

Any database populated with the retired sequence-only M1.6 identity must also be replayed after the V2 strict identity change. Those item keys are not compatible and must not be mixed.

For development, use a clean replay from authoritative raw ZIP files. `scripts/reset-m16.ps1` preserves raw data, copies archived CN ZIPs back to the incoming replay queue, recreates the databases, and starts PostgreSQL/ClickHouse/API without the worker.

## Crash recovery and member checkpoints

CN ingestion now uses a PostgreSQL session advisory lock for the whole candidate-selection through publish cycle. If Python, the API container, Docker Desktop, or the host dies, PostgreSQL releases the lock automatically. On the next run, any orphaned `PROCESSING` package is marked `INTERRUPTED` and re-enters the queue by `source_rank`, so an older interrupted partition cannot be silently skipped by newer `REGISTERED` work.

M1.6 also adds `CN_PACKAGE_MEMBER_CHECKPOINT_V1`. `control.source_package_file` is the durable checkpoint marker because it is written only after one ZIP member has been fully parsed. ClickHouse stage rows for checkpointed members are preserved across interruption. Any rows belonging to an uncheckpointed member are treated as potentially partial and are deleted synchronously before retry. The retry skips only checkpointed members whose retained stage rows still exist; stale metadata without stage is invalidated and reparsed.

This is member-level resume rather than arbitrary row-level resume. A crash in the middle of one very large internal CSV still requires that one internal member to be reparsed from its beginning, but already completed members in the same ZIP are not repeated. If a crash happens during publication after every member was checkpointed, the next run can reuse the complete stage set, clean partial published outputs, and rerun publication without reparsing the ZIP.

## Validation order

1. `scripts/reset-m16.ps1`
2. `scripts/validate-cn-contract.ps1`
3. `scripts/validate-cn-fixture.ps1`
4. `scripts/validate-m16-goods.ps1`
5. run `audit-m16-goods-identity.ps1` against the first real base package
6. replay base packages in source order
7. validate an intentionally interrupted package resumes from member checkpoints
8. run integrity audit
9. only then test monthly packages
10. only after monthly delta validation start the worker

The M1.6 goods fixture explicitly creates three baseline goods, applies a monthly patch containing only one changed item, and requires all three items to remain present after the patch.

## Case inference remains separate

M1.6 does not claim that a goods code proves why a trademark lost rights. A later case inference engine may combine filing date, preliminary publication, registration publication, validity dates, complete goods-scope changes, opposition/review/invalidation/cancellation decisions, and court judgments. Such output must be versioned, evidence-linked, confidence-scored, and clearly separated from official facts.
