# Data Engine Storage V2

## Purpose

Storage V2 keeps MarkOrbit Data Engine evidentiary semantics while preventing no-op source re-observations from becoming permanent history.

The first Storage V2 change is intentionally narrow and non-destructive. It changes future CN goods observation writes and adds read-only diagnostics. It does not delete existing facts, compact the live database, run `OPTIMIZE`, reset replay state, or change legal interpretation rules.

## Three storage layers

1. **Raw authority** — official source packages remain the reproducible source evidence and are kept outside ClickHouse in `RAW_DATA_ROOT`.
2. **Current fact state** — query-facing current tables retain the latest accepted source state and provenance.
3. **Delta history** — history tables retain first observations and real changes, not repeated identical observations.

Raw evidence and derived/current storage serve different purposes. Retaining an official source package does not require duplicating every unchanged fact from that package into permanent hot history.

## CN goods observation policy

`cn_goods_item_observation` persists these transitions:

- `FIRST_OBSERVED`
- `STATUS_CHANGED`
- `ITEM_DETAILS_CHANGED`

An unchanged later appearance of the same durable goods item is a no-op for fact history. Storage V2 therefore does **not** persist `REOBSERVED` rows going forward.

This does not mean that a monthly omission is deletion. Existing M1.6 rules remain unchanged:

- full application number is the case identity;
- monthly patches override base data according to source semantics and source rank;
- omission from a monthly package is not deletion;
- `FIRST_OBSERVED` is evidence of first observation, not a legal event date;
- status semantics remain empirical unless separately validated.

`cn_goods_item_current` continues to accept the latest source provenance under the existing source-rank rules. This first Storage V2 step deliberately does not redefine `last_source_*` semantics.

## Read-only storage audit

Run the physical audit without starting the persistent worker:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-storage.ps1
```

The default audit reads `system.parts` only and reports active/inactive rows and bytes by table.

For logical CN history distributions:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-storage.ps1 -Deep
```

Deep mode additionally groups:

- `cn_goods_item_observation` by `transition_type`;
- `cn_observed_event` by `event_type`.

Deep mode can scan very large history tables. It remains read-only, but should be run when the corpus is idle and enough I/O headroom is available.

## What this version does not do

Storage V2 V1 does not:

- delete legacy `REOBSERVED` rows;
- mutate or optimize the live ClickHouse volume;
- shrink the Docker/WSL VHDX;
- remove `FIRST_OBSERVED` history;
- deduplicate legal/source facts by guesswork;
- change CN replay ordering or acceptance gates;
- change US, Assignment, or TTAB semantics.

Any later historical compaction must first prove that the affected rows are reconstructible from retained source packages and that no evidentiary or chronology semantics are lost.
