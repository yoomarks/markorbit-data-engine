# CN Goods Lifecycle Model V2

Status: DESIGN FREEZE CANDIDATE

This document captures the empirically observed semantics of CN goods status codes and the required ingestion model. These semantics are domain observations and must remain separately labelled from official legal facts until independently verified against authoritative outcome data.

## 1. Core observation

The goods status field is not a simple current active/inactive flag.

Observed interpretation:

- blank / no negative code: no negative goods-status signal observed.
- `0`: the item has been, or may currently be, in a renewal grace / transition-risk condition. The item may later recover (for example after a late renewal) and therefore MUST NOT be treated as finally invalid.
- `1`: the item has passed a renewal/grace deadline or another loss-of-rights condition, but the office has not yet completed the final deletion/invalidation acknowledgement. Operationally it is likely ineffective, but it is not yet a final official inactive signal.
- `2`: a final negative outcome has been observed for the item, including non-renewal, cancellation, non-use cancellation, invalidation, voluntary cancellation, refusal or another finally effective loss event. Code `2` identifies final item inactivity; it does NOT by itself identify the legal cause.

The system must preserve the raw code and distinguish observed fact from inferred legal effect.

## 2. Monthly packages are deltas, not snapshots

A base/full package establishes the complete goods universe known for a case/class at that baseline.

A later monthly package contains only goods whose records changed. Therefore:

- omission from a monthly package MUST NOT delete an existing goods item;
- a monthly package MUST NOT replace the complete class scope;
- only touched goods items are updated;
- untouched goods items retain their previous state;
- the current class scope is reconstructed from durable item-level current state after applying all deltas in source-rank order.

This rule is mandatory. Treating a monthly package as a complete scope snapshot will silently erase valid goods.

## 3. Required durable item model

Introduce a durable item-level current table, conceptually:

`cn_goods_item_current`

Minimum logical fields:

- case_id
- application_number
- class_no
- goods_item_key
- goods_sequence_raw
- goods_name_raw
- goods_name_norm
- similar_group
- status_code_raw
- status_semantic
- status_finality
- source_package_kind
- source_effective_date
- source_file
- source_first_line
- source_last_line
- source_row_hash
- last_source_package_id
- source_rank
- is_deleted

Also retain an append-oriented observation/history table, conceptually:

`cn_goods_item_observation`

Every base observation and monthly change should be traceable.

## 4. Status semantics

Recommended semantic values:

| raw code | semantic state | finality | treatment |
|---|---|---|---|
| blank | NO_NEGATIVE_SIGNAL | OPEN | count as currently effective unless contradicted by stronger evidence |
| 0 | AT_RISK_OR_GRACE | REVERSIBLE | do not remove from effective scope; surface risk |
| 1 | PENDING_INACTIVE | PROVISIONAL | likely ineffective operationally, but not final official deletion |
| 2 | FINAL_INACTIVE | FINAL | remove from effective goods scope |
| other | UNKNOWN | UNKNOWN | preserve raw code; no destructive inference |

The mapping must carry a version such as `CN_GOODS_STATUS_V2_LIFECYCLE` and an evidence label such as `EMPIRICAL_DOMAIN_MAPPING`.

## 5. Scope reconstruction

For each application number + class:

- baseline_item_count: number of distinct goods established by the full/base observation;
- effective_item_count: goods not in FINAL_INACTIVE;
- final_inactive_item_count: status `2`;
- pending_inactive_item_count: status `1`;
- at_risk_item_count: status `0`;
- unknown_item_count: unsupported codes;
- partial_loss_flag: final_inactive_item_count > 0 AND effective_item_count > 0;
- full_final_loss_flag: baseline_item_count > 0 AND final_inactive_item_count = baseline_item_count;

A monthly package that mentions only two changed goods must update those two items and then recompute these counts over the complete durable item set.

## 6. Case-level inference

Goods status provides strong evidence but must not be overclaimed.

Suggested inferred states:

- all known goods FINAL_INACTIVE -> `ALL_GOODS_FINAL_INACTIVE`
- some goods FINAL_INACTIVE, some remain -> `PARTIAL_GOODS_FINAL_INACTIVE`
- no code 2 but one or more code 1 -> `LIKELY_INACTIVE_PENDING_OFFICIAL`
- one or more code 0 -> `AT_RISK_OR_GRACE`
- otherwise -> `NO_NEGATIVE_GOODS_SIGNAL`

These are inferred operational states, not official case status labels.

The cause of a code-2 loss MUST remain `UNKNOWN` unless another official source identifies refusal, cancellation, non-use cancellation, invalidation, non-renewal, voluntary cancellation, etc.

## 7. Refusal / cancellation interpretation

The goods lifecycle can materially improve outcome inference:

- if all goods eventually become code `2`, the case/class has a strong final-loss signal;
- if only part of the goods become code `2`, the system should infer a partial adverse result rather than total case invalidity;
- if code `0` later returns to a non-negative state, this is evidence of a reversible grace/renewal recovery path;
- transitions `0 -> 1 -> 2`, `1 -> 2`, `0 -> recovered`, and partial subsets changing to `2` should be explicitly measured once monthly data is loaded.

## 8. Required transition validation

Before promoting the mapping from empirical to verified, build a longitudinal analyzer over monthly packages and report:

- 0 -> blank / recovered
- 0 -> 1
- 0 -> 2
- 1 -> 2
- 1 -> recovered
- 2 -> any non-2 value (should be rare and investigated)
- partial item subset -> 2
- all items -> 2

Cross-check samples against known renewal, cancellation, invalidation, refusal and other official events when available.

## 9. Migration rule for the current M1.5 model

The current class-level scope aggregate is safe for base partitions but is not sufficient for monthly delta ingestion.

Do NOT implement monthly goods patches by overwriting `cn_case_scope_current` from only the monthly rows.

Required migration order:

1. add item-level durable current/history tables;
2. seed them from existing base partitions;
3. derive class scope from item current state;
4. make MONTHLY_PATCH perform item-level upsert only;
5. recompute touched class scopes from the complete item current set;
6. only then enable automated monthly ingestion;
7. keep raw status codes and mapping version permanently auditable.

## 10. Design invariant

**Base package defines the known full goods set. Monthly package patches only changed goods. Omission is never deletion. Final case/class loss may be inferred only from the reconstructed complete goods set, never from the contents of one monthly patch.**
