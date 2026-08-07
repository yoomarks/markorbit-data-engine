# CN Goods Lifecycle Model V2

Status: DESIGN FREEZE CANDIDATE

This document captures the empirically observed semantics of CN goods status codes and the required ingestion model. The codes describe the state of individual goods/service items. They MUST NOT be treated as direct trademark/case status codes and MUST NOT be used by themselves to infer the legal cause of loss.

## 1. Core observation

The goods status field is an item-level state signal, not a trademark-status field.

Observed interpretation:

- blank / no negative code: no negative goods-status signal observed for that item.
- `0`: a reversible or unresolved adverse/risk state has been observed for the item. It MUST NOT be treated as finally inactive.
- `1`: the item is operationally highly likely to be inactive, but the source may leave it at code `1` indefinitely. Code `1` does not require a later transition to `2`.
- `2`: a stronger/final inactive state has been observed for the item.

Critical rule:

**Codes `0`, `1`, and `2` describe goods/service item state only. They do not by themselves state the trademark's overall legal status and they do not identify why the item became inactive.**

Possible legal causes such as non-renewal, refusal, cancellation, non-use cancellation, invalidation, voluntary cancellation, partial adverse decision, or other loss events MUST come from separate official evidence/events.

The system must preserve the raw code and distinguish observed item fact from any later inference.

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

Recommended neutral semantics:

| raw code | semantic state | source finality | operational effect | treatment |
|---|---|---|---|---|
| blank | NO_NEGATIVE_SIGNAL | OPEN | EFFECTIVE_UNLESS_CONTRADICTED | keep in effective scope unless stronger evidence exists |
| 0 | REVERSIBLE_OR_UNRESOLVED_RISK | REVERSIBLE | EFFECTIVE_AT_RISK | keep in effective scope; surface item-level risk |
| 1 | INACTIVE_HIGH_CONFIDENCE | SOURCE_NOT_FINALIZED | INACTIVE_HIGH_CONFIDENCE | normally exclude from operationally effective scope; preserve raw `1`; do not invent a legal cause |
| 2 | FINAL_INACTIVE | FINAL | INACTIVE_CONFIRMED | exclude from effective scope; preserve raw `2`; do not invent a legal cause |
| other | UNKNOWN | UNKNOWN | UNKNOWN | preserve raw code; no destructive inference |

The mapping must carry a version such as `CN_GOODS_STATUS_V2_LIFECYCLE` and an evidence label such as `EMPIRICAL_DOMAIN_MAPPING`.

### Important distinction for code `1`

Code `1` is not merely a temporary state waiting to become `2`.

Observed behavior indicates that code `1` may remain terminal in the source dataset. Therefore:

- do not require a `1 -> 2` transition;
- do not keep code-1 goods in the operationally effective scope merely because the public case status is unresolved;
- do preserve the distinction between code `1` and code `2`;
- do not infer non-renewal, cancellation, invalidation, refusal, or any other legal cause from code `1` alone;
- do not infer the trademark's overall status from code `1` alone.

## 5. Scope reconstruction

For each application number + class:

- baseline_item_count: number of distinct goods established by the full/base observation;
- operational_effective_item_count: goods whose current operational effect is `EFFECTIVE_UNLESS_CONTRADICTED` or `EFFECTIVE_AT_RISK`;
- risk_item_count: status `0`;
- inactive_high_confidence_item_count: status `1`;
- final_inactive_item_count: status `2`;
- inactive_total_item_count: status `1` + status `2`;
- unknown_item_count: unsupported codes;
- partial_inactive_scope_flag: inactive_total_item_count > 0 AND operational_effective_item_count > 0;
- all_known_goods_inactive_flag: baseline_item_count > 0 AND inactive_total_item_count = baseline_item_count;
- all_known_goods_final_inactive_flag: baseline_item_count > 0 AND final_inactive_item_count = baseline_item_count.

These are goods-scope facts/inferences only. They are not trademark-status labels.

A monthly package that mentions only two changed goods must update those two items and then recompute these counts over the complete durable item set.

## 6. Trademark/case status must remain separate

Goods status can support later reasoning, but the trademark/case status model must remain a separate layer.

Allowed goods-scope conclusions include:

- `SOME_GOODS_INACTIVE`
- `ALL_KNOWN_GOODS_INACTIVE`
- `SOME_GOODS_FINAL_INACTIVE`
- `ALL_KNOWN_GOODS_FINAL_INACTIVE`
- `GOODS_RISK_SIGNAL_PRESENT`

These labels describe the reconstructed goods scope only.

The system MUST NOT convert them directly into trademark/case conclusions such as:

- `TRADEMARK_EXPIRED`
- `TRADEMARK_CANCELLED`
- `TRADEMARK_INVALIDATED`
- `TRADEMARK_REFUSED`
- `TRADEMARK_NONRENEWED`

Such conclusions require separate official case-level evidence or a separately versioned inference model using additional evidence.

## 7. Cause attribution is forbidden from goods code alone

Neither code `1` nor code `2` identifies the legal reason for inactivity.

The cause field must remain `UNKNOWN` unless another official source identifies the event, for example:

- refusal / partial refusal;
- cancellation;
- non-use cancellation;
- invalidation;
- voluntary cancellation;
- expiration / non-renewal;
- other legally effective loss event.

Even when every known goods item is code `1` or `2`, the correct goods-layer statement is only that all known goods are inactive at the observed confidence/finality level. The trademark's legal status and the cause remain separate questions.

## 8. Required transition validation

Build a longitudinal analyzer over base and monthly packages and report item-level transitions:

- blank -> 0
- blank -> 1
- blank -> 2
- 0 -> blank / recovered
- 0 -> 1
- 0 -> 2
- persistence duration of code `1`
- 1 -> 2 (do not assume this is required)
- 1 -> non-1 state
- 2 -> any non-2 value (rare; investigate)
- partial item subset -> inactive
- all known items -> inactive
- all known items -> code `2`

This analyzer validates data behavior only. It must not assign legal causes unless joined to separate official events/notices.

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
7. keep raw status codes and mapping version permanently auditable;
8. keep goods-scope state, trademark/case status, and legal cause as three separate concepts.

## 10. Design invariant

**Base package defines the known full goods set. Monthly package patches only changed goods. Omission is never deletion. Codes `0/1/2` describe goods-item state only. Goods-scope inactivity, trademark/case status, and legal cause are separate layers and MUST NOT be collapsed into one another.**
