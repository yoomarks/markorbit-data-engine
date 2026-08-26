# CN storage serving and reconstructibility contract

This document is a safety boundary for storage-capacity work. It does not authorize live migration, deletion, mutation, `OPTIMIZE`, table exchange, or cutover.

## Current serving constraints

The current CN API directly reads `cn_goods_item_current`, `cn_case_party_current`, and `cn_observed_event` for case-detail payloads. Those active tables are therefore Hot dependencies until a serving-compatible replacement exists.

`cn_goods_item_observation` is currently referenced by the CN summary endpoint for its row count. Its retained true-delta history can be a Warm candidate only after that summary dependency is preserved or replaced.

`cn_case_party_relation_history` is not a direct case-detail API payload in the current static contract scan. Storage V2 already suppresses new duplicate wide-history writes because canonical OWNER/CO_OWNER/AGENT observations and supersessions are retained in `cn_observed_event`. Existing legacy relation-history rows remain a verification-first Warm/compaction candidate, not an authorized deletion target.

## Reconstructibility boundaries

For goods history, `cn_goods_item_current` retains durable first-source provenance. `FIRST_OBSERVED` can therefore be reconstructed from retained official raw authority plus current first-source fields; `REOBSERVED` is a no-op. `STATUS_CHANGED` and `ITEM_DETAILS_CHANGED` remain durable delta history.

For observed events, baseline-only `APPLICATION_OBSERVED`, `GOODS_SCOPE_OBSERVED`, and `DERIVED_CASE_OBSERVED` records, plus first publication/registration/term observations with empty prior values, are reconstructible candidates. Party relation events and events carrying prior-state evidence remain durable history.

Current-state serving tables can be rebuilt by the retained official-source replay path, but rebuildability is not a reason to move them out of Hot serving storage while the API still reads them directly.

## Decision rule before US/global scale-out

1. Preserve direct serving contracts first.
2. Compact only independently proven duplicate/reconstructible baseline history.
3. Require a replacement serving projection before demoting a directly served current/history table.
4. Require rollback/rebuild evidence before destructive finalization.
5. Keep SG/US full-corpus rollout blocked until storage headroom and the resulting Hot footprint support the next corpus safely.

Run `scripts/audit-storage-consumers.ps1` to generate the machine-readable static consumer inventory. Static absence is not proof of no runtime consumer because dynamic SQL may hide references.
