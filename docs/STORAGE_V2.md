# Data Engine Storage V2

## Purpose

Storage V2 keeps MarkOrbit Data Engine evidentiary semantics while preventing baseline or no-op source observations from becoming permanent wide history.

The storage model is:

1. **Raw authority** — official source packages remain the reproducible source evidence and are kept outside ClickHouse in `RAW_DATA_ROOT`.
2. **Current fact state** — query-facing current tables retain the latest accepted source state and provenance.
3. **True delta history** — wide history tables retain real state changes, not a second copy of the initial corpus.

Raw evidence and derived/current storage serve different purposes. Retaining an official source package does not require duplicating every unchanged fact from that package into permanent hot history.

## CN goods observation policy

`cn_goods_item_current` already stores durable first-observation provenance:

- `first_source_package_id`
- `first_source_package_kind`
- `first_source_rank`

Together with the retained authoritative raw package and the durable goods item identity, those fields make the first observation reconstructible without a second full-width goods-history row.

Storage V2 therefore reserves `cn_goods_item_observation` for true changes only:

- `STATUS_CHANGED`
- `ITEM_DETAILS_CHANGED`

`FIRST_OBSERVED` and `REOBSERVED` are not persisted by the M1.6 runtime path going forward. `REOBSERVED` is a no-op; first-observation package provenance lives on current state.

This does not mean that a monthly omission is deletion. Existing M1.6 rules remain unchanged:

- full application number is the case identity;
- monthly patches override base data according to source semantics and source rank;
- omission from a monthly package is not deletion;
- first observation is evidence of first observation, not a legal event date;
- status semantics remain empirical unless separately validated.

## CN party relation history policy

`cn_case_party_relation_history` is permanent relation history, not a package-level observation log. Storage V2 persists `OBSERVED_CURRENT` only when a relation is:

- first observed;
- restored after a prior supersession; or
- materially changed (`record_hash` differs under a newer accepted source rank).

`SUPERSEDED` remains change-driven under the existing publisher rules. A later package that repeats an identical current OWNER, CO_OWNER, or AGENT relation does not append another permanent `OBSERVED_CURRENT` row.

A later Storage V2 phase may compact legacy first-observation party rows only after compact provenance coverage is proven. The current party-history change affects future growth only.

## CN observed-event policy

`cn_observed_event` is a change/evidence stream, not a second copy of every first-seen current fact. Storage V2 does not persist these reconstructible baseline-only events going forward:

- `APPLICATION_OBSERVED`;
- `GOODS_SCOPE_OBSERVED`;
- `DERIVED_CASE_OBSERVED`;
- first `PRELIMINARY_PUBLICATION_OBSERVED` with an empty old value;
- first `REGISTRATION_PUBLICATION_OBSERVED` with an empty old value;
- first `EXCLUSIVE_TERM_OBSERVED` with an empty old value.

The event table continues to retain:

- all events carrying non-empty prior-state evidence;
- case/goods/term/name/agent-code change events;
- OWNER, CO_OWNER, and AGENT relation observed/superseded events.

Party relation events remain intentionally out of this compaction phase. Their first-vs-later lineage is handled separately so event compaction cannot silently erase party evidence.

Read-only plan:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-observed-event.ps1 -Mode Plan
```

The plan fails closed on unknown event types or a leftover temporary shadow. Commit is single-process and revalidates a count-plus-event-hash fingerprint before and after the atomic table exchange:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-observed-event.ps1 -Mode Commit
```

If execution is interrupted around the atomic exchange, rerunning the same Commit command only resumes a structurally proven pre-exchange shadow or post-exchange pending-drop state. The final validated DROP uses a query-scoped `max_table_size_to_drop=0`; it does not change ClickHouse global configuration or create a force-drop file.

Status is read-only:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-observed-event.ps1 -Mode Status
```

## Read-only storage audit

Run the physical audit without starting the persistent worker:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-storage.ps1
```

For logical CN history distributions:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-storage.ps1 -Deep
```

Deep mode reports:

- goods observation transitions;
- CN observed-event rows split by event type and whether an old value exists;
- reconstructible event-baseline candidate rows;
- party history actions.

Party observed events are deliberately excluded from automatic event-baseline classification until first-vs-later relation rank can be proven.

Deep mode remains read-only, but it can scan very large history tables and should be run while corpus replay is idle.

## Guarded goods-history compaction

The goods compactor is deliberately reversible until finalization.

Read-only plan:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-goods-history.ps1 -Mode Plan
```

The plan fails closed if:

- an unknown goods transition exists;
- any current goods item lacks `first_source_package_id` or `first_source_rank`;
- a prior compaction shadow/archive is present.

Apply the compact table while keeping the original wide table as a rollback archive:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-goods-history.ps1 -Mode Apply
```

Rollback before finalization:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-goods-history.ps1 -Mode Rollback
```

After audits confirm the compact table is correct, finalization drops only the archived wide goods-observation table:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-goods-history.ps1 -Mode Finalize
```

Finalization is the irreversible ClickHouse space-reclaim step. It does not delete raw authority packages or current goods state.

The ClickHouse filesystem can immediately reuse released blocks. The outer Docker/WSL `docker_data.vhdx` file can remain physically large until a separate filesystem/VHDX compaction is performed.

## Safety boundaries

Storage V2 does not:

- run CN replay automatically;
- reset the corpus;
- infer deletion from monthly omission;
- remove true goods changes;
- remove party relation events during observed-event compaction;
- alter US Application, Assignment, or TTAB semantics;
- modify Core/Gateway repositories;
- shrink or rewrite the Docker VHDX as part of database compaction.

Historical compaction is allowed only where retained raw authority plus durable current provenance can reconstruct the removed baseline evidence and the compactor validates that every true delta remains present.
