# CN Goods Lifecycle Model V2

Status: DESIGN FREEZE CANDIDATE

This document captures the empirically observed semantics of CN goods status codes and the required ingestion model. These semantics are domain observations and must remain separately labelled from official legal facts until independently verified against authoritative outcome data.

## 1. Core observation

The goods status field is not a simple current active/inactive flag.

Observed interpretation:

- blank / no negative code: no negative goods-status signal observed.
- `0`: the item has been, or may currently be, in a renewal grace / transition-risk condition. The item may later recover (for example after a late renewal) and therefore MUST NOT be treated as finally invalid.
- `1`: the item is strongly associated with expiration for non-renewal after the ordinary/grace renewal period has passed. In observed practice, these cases have generally already received a non-renewal invalidation/expiration notice, even though CNIPA's public status may remain unresolved/question-mark-like and the goods code may remain `1` indefinitely instead of being promoted to `2`. Operationally, code `1` should therefore be treated as a high-confidence inactive/non-renewed signal, but the system must preserve that it is not the same source code as `2` and must not invent a specific final administrative status label that the source did not provide.
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
- operational_effect
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

| raw code | semantic state | source finality | operational effect | treatment |
|---|---|---|---|---|
| blank | NO_NEGATIVE_SIGNAL | OPEN | EFFECTIVE_UNLESS_CONTRADICTED | count as currently effective unless contradicted by stronger evidence |
| 0 | RENEWAL_GRACE_OR_RISK | REVERSIBLE | EFFECTIVE_AT_RISK | do not remove from effective scope; surface renewal/grace risk |
| 1 | EXPIRED_NONRENEWAL_PENDING_SOURCE_CLOSE | SOURCE_NOT_FINALIZED | INACTIVE_HIGH_CONFIDENCE | normally exclude from operationally effective scope; preserve code `1` and do not relabel it as code `2` |
| 2 | FINAL_INACTIVE | FINAL | INACTIVE_CONFIRMED | remove from effective goods scope |
| other | UNKNOWN | UNKNOWN | UNKNOWN | preserve raw code; no destructive inference |

The mapping must carry a version such as `CN_GOODS_STATUS_V2_LIFECYCLE` and an evidence label such as `EMPIRICAL_DOMAIN_MAPPING`.

### Important distinction for code `1`

Code `1` is **not** merely a transient "pending inactive" state that must later transition to `2`.

Observed behavior indicates that code `1` can be terminal in the source dataset: the trademark has effectively expired for non-renewal, a non-renewal invalidation/expiration notice has generally been issued, but the office/public database may not convert the goods record to code `2` and may leave the overall status unresolved.

Therefore:

- do not require a `1 -> 2` transition;
- do not keep code-1 goods in the operationally effective scope merely because the public status is unresolved;
- do preserve a distinction between `INACTIVE_HIGH_CONFIDENCE` (`1`) and `INACTIVE_CONFIRMED` (`2`);
- if another official event later proves renewal/restoration, that stronger later evidence may reactivate the item.

## 5. Scope reconstruction

For each application number + class:

- baseline_item_count: number of distinct goods established by the full/base observation;
- operational_effective_item_count: goods whose current operational effect is `EFFECTIVE_UNLESS_CONTRADICTED` or `EFFECTIVE_AT_RISK`;
- renewal_risk_item_count: status `0`;
- nonrenewal_inactive_item_count: status `1`;
- final_inactive_item_count: status `2`;
- inactive_high_confidence_item_count: status `1` + status `2`;
- unknown_item_count: unsupported codes;
- partial_final_loss_flag: final_inactive_item_count > 0 AND operational_effective_item_count > 0;
- all_operationally_inactive_flag: baseline_item_count > 0 AND inactive_high_confidence_item_count = baseline_item_count;
- full_final_loss_flag: baseline_item_count > 0 AND final_inactive_item_count = baseline_item_count.

A monthly package that mentions only two changed goods must update those two items and then recompute these counts over the complete durable item set.

## 6. Case-level inference

Goods status provides strong evidence but must not be overclaimed.

Suggested inferred states:

- all known goods code `2` -> `ALL_GOODS_FINAL_INACTIVE`
- some goods code `2`, some remain operationally effective -> `PARTIAL_GOODS_FINAL_INACTIVE`
- all known goods are code `1` and/or `2`, with at least one code `1` -> `ALL_GOODS_OPERATIONALLY_INACTIVE_HIGH_CONFIDENCE`
- one or more code `1`, while other goods remain effective -> `PARTIAL_NONRENEWAL_INACTIVE_HIGH_CONFIDENCE`
- one or more code `0` -> `RENEWAL_GRACE_OR_RISK`
- otherwise -> `NO_NEGATIVE_GOODS_SIGNAL`

These are inferred operational states, not official case status labels.

The cause of a code-2 loss MUST remain `UNKNOWN` unless another official source identifies refusal, cancellation, non-use cancellation, invalidation, non-renewal, voluntary cancellation, etc.

For code `1`, the inferred cause may be `NONRENEWAL_EXPIRATION_HIGH_CONFIDENCE` when supported by the empirical source behavior, but it must remain labelled as an inference unless a separate official event/notice confirms the cause for that case.

## 7. Refusal / cancellation / non-renewal interpretation

The goods lifecycle can materially improve outcome inference:

- if all goods eventually become code `2`, the case/class has a strong final-loss signal;
- if only part of the goods become code `2`, the system should infer a partial adverse result rather than total case invalidity;
- if all goods are code `1`, the system should infer a strong non-renewal expiration condition even if the public database status remains unresolved;
- if code `0` later returns to a non-negative state, this is evidence of a reversible grace/renewal recovery path;
- transitions `0 -> 1`, `0 -> 2`, direct blank -> `2`, persistent `1`, and partial subsets changing to `2` should all be measured once monthly data is loaded.

## 8. Required transition validation

Before promoting the mapping from empirical to verified, build a longitudinal analyzer over monthly packages and report:

- blank -> 0
- blank -> 1
- blank -> 2
- 0 -> blank / recovered
- 0 -> 1
- 0 -> 2
- persistence duration of code `1`
- 1 -> 2 (do not assume this is required)
- 1 -> recovered / renewed
- 2 -> any non-2 value (should be rare and investigated)
- partial item subset -> 2
- all items -> 1/2
- all items -> 2

Cross-check samples against known renewal, non-renewal expiration, cancellation, invalidation, refusal and other official events when available.

A particularly important empirical test is whether code `1` correlates with expired `valid_until` dates and known non-renewal notices while remaining code `1` for long periods. If confirmed at scale, this supports treating code `1` as operationally inactive without requiring a later code `2`.

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

**Base package defines the known full goods set. Monthly package patches only changed goods. Omission is never deletion. Code `1` is a high-confidence non-renewal inactivity signal that may remain terminal in the source data; code `2` is a confirmed final inactive goods signal. Final case/class conclusions must be inferred from the reconstructed complete goods set, never from the contents of one monthly patch.**
