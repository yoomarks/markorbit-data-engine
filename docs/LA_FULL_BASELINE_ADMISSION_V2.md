# LA full-baseline Data Engine admission V2 — gated contract

This is the downstream receiver contract only for Knowledge #907. It does
not implement WoPublish HTTP acquisition, grant a full-collection Work, create
Knowledge RawArtifacts, or declare the Lao baseline complete. #903's real
50+50+1 authenticated Knowledge-to-owner hot_global receipt and replay must
pass first. Production GLOBAL_HOT_FULL_BASELINE_ENABLED defaults to false.

## Source and shape

V1 pilot remains unchanged: GLOBAL_TRADEMARK_STRUCTURED_ADMISSION_V1 accepts
exactly two 50-ID list pages and one explicit detail. V2 is a separate
GLOBAL_TRADEMARK_STRUCTURED_ADMISSION_V2 contract and allows only:

- FULL_INDEX_PAGE: 1-based page at the official 50-record size. Supply the
  source-observed integer source_total (101–100,000), page index 1 through
  ceil(source_total/50), and exactly 50 real IDs except the last page.
- FULL_DETAIL: one evidenced, publicly retrievable LA source ID, page index
  0 and no source_total. A frozen Knowledge full-index manifest and signed
  cross-Source grant are mandatory before preparing the detail fact.

Evidence uses the existing redacted Knowledge canonical URI for the exact page
or detail, exact source response and redacted evidence SHA-256, observation
instant, LA source identity and existing normalized field rules. No guessed
sequential source IDs or inferred legal registration numbers. Unverified
status remains raw. Even accepted details do not establish legal currentness.
Data Engine does not fetch the source or access the Knowledge database.

## Physical schema and separate enablement

A new global hot table is created with page_index UInt16. Already accepted
production tables may still have V1 UInt8. The receiver checks actual
system.columns on every V2 admission and refuses until the operator applies
a separately reviewed, exclusive production migration.

Operator-only migration SQL, NOT executed by this PR:

    ALTER TABLE markorbit_facts.global_trademark_hot_observation
      MODIFY COLUMN page_index UInt16;

First freeze fact-admission jobs, confirm the exact WSL production target,
back up/check the accepted table, verify E-disk hot_global_only placement
and 20% reserve, and record before/after schema and row-count receipts.
Never send DDL through HTTP or change CN/US schema or source storage.

Only after #903 pilot acceptance and source-policy/cost review may the operator
separately set GLOBAL_HOT_FULL_BASELINE_ENABLED=true in managed owner config.
Restart only the isolated owner service. Unauthorized requests retain HTTP
401. V2 remains blocked by BOTH that switch and the actual UInt16 schema;
V1 remains available while full baseline is disabled.

## Completion and failure boundaries

Full coverage requires frozen true-ID manifest, bounded request budgets,
per-page durable redacted RawArtifact, approved Workspace/Source/lease and
cross-Source publisher grant, true owner receipts, replay/readback and all
accepted details (and permitted logo evidence). Drift, missing pages or
unsuccessful details remain PARTIAL with auditable gaps. Do not mark baseline
complete from Data Engine row counts alone. Periodic LA collection stays
disabled until a later, separate maintenance activation.
