# Changelog

## M1.6 / 0.4.0

- Added durable `cn_goods_item_current` state with strict source-field identity.
- Added item-level status observations and explicit transition lineage.
- Rebuilt touched monthly class scopes from the complete durable item universe so omission is never deletion.
- Added goods lifecycle aggregation for effective, risk, inactive, final-inactive and unknown evidence states.
- Added source-backed M1.6 acceptance, monthly-patch and goods-identity audits.
- Added empirical R1-R7 CN case-status inference as a separate, reversible layer; no heuristic is stored as an official fact.
- Anchored historical inference to loaded source coverage rather than wall-clock time and excluded `FIRST_OBSERVED` from legal-event timing.
- Added deterministic SHA-256 bottom-k review sampling per rule.
- Added file-based manual ground-truth review packets with official-source requirements and per-rule precision/coverage scoring.
- Added GitHub Actions CI for Python 3.12, Ruff and the complete pytest suite.
- Centralized runtime release metadata on the repository `VERSION` marker and exposed M1.6 durable goods state through the API summary/case endpoints.
- Added current M1.6 reset/runtime validation entry points; M1.5 script names are retained only as legacy delegates.

## M1.5 / 0.3.0

- Rebuilt the CN permanent field model.
- Added source-semantic precedence for base partitions and monthly patches.
- Preserved all supplied official basic fields permanently.
- Added conservative goods-status interpretation with unmapped-code accounting.
- Added case-party relation current/history tables and exact entity candidates.
- Added explainable observed events with source file and logical row evidence.
- Added CN direct and Madrid-designation number routes in one CN case model.
- Added derived-case and scope-carve-out evidence tables.
- Added Date32 chronology checks and cross-table quality issues.
- Added M1.5 dashboard, case inspection and raw UTF-8 audit export.
- Consolidated M1.1-M1.4 operational fixes.
