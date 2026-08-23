# Singapore IPOS Activation Record

## Status

Source/lifecycle activation is accepted on the real public corpus. Generic snapshot/delta behavior and current projection plumbing are implemented. Source-native fact extraction covers the current 39-column IPOS source schema, and updated applications can now be decomposed into deterministic neutral source-family changes with durable source evidence; deeper semantic normalization remains follow-on work.

A recurring production acquisition schedule is not asserted by this record.

## Source

Authority: Intellectual Property Office of Singapore (IPOS)

Canonical source identity:

- jurisdiction: `SG`
- source ID: `IPOS_SG_TRADEMARK_APPLICATIONS`
- dataset ID: `d_6145acb2130bf781165258e76a584383`
- file: `IPOSTradeMarkApplications.csv`
- source type: `current_snapshot`

The Singapore jurisdiction declaration is the canonical identity contract. Provider-specific data.gov.sg acquisition URLs remain in the Singapore snapshot adapter rather than in generic snapshot primitives.

## Phase 1 — Source and Lifecycle Activation

Implemented and accepted:

- dataset metadata/source contract
- data.gov.sg download/API strategy
- schema capture
- snapshot manifest generation
- deterministic content/schema hashing
- accepted-current snapshot lifecycle
- durable manifest/provenance handling
- atomic current pointer/manifest validation
- controlled full-corpus acceptance

The operator-grade full-corpus acceptance requires exactly one accepted current full CSV in the lifecycle state, a matching manifest/current pointer, non-empty row and byte counts, and durable event evidence whenever the lifecycle reports `CHANGED`.

Downloaded snapshots are validated against both the critical application/status columns and the complete authoritative 39-column source contract before the partial file can replace the accepted snapshot. Both official datastore field ids and official CSV display headings normalize to the same contract. Missing or newly introduced source columns fail closed, while data.gov.sg datastore `_id` remains provider metadata rather than an IPOS native fact.

The snapshot lifecycle is also an explicit acceptance boundary: it re-validates the complete source contract even when acquisition is supplied by an alternate or custom downloader. This prevents a caller from bypassing adapter-level validation and prevents an incomplete schema from advancing the accepted-current pointer or replacing persisted source evidence. The lightweight live-source probe applies the same complete contract to the authoritative datastore field list, so source drift can be detected without downloading the multi-gigabyte corpus.

## Phase 2 — Native Semantic Extraction

Current source schema coverage is implemented without introducing legal interpretation.

The extractor requires authoritative application identity and mark status, preserves source date values as source strings, and parses source JSON-array families without changing their nested source structure. It accepts both official API field names and the corresponding CSV display headings.

The current 39-column source contract covers:

- application number, filing date, international registration date and Singapore protection date
- series mark number, application type, trade mark type and particular-feature description
- application date, mark status and source status/update dates
- registration completion, expiry, publication and last-modified dates
- journal, IR and IA details
- transformation, transformation-into, replacement and replacement-replaces data
- priority data and priority-claim details
- mark clauses, mark data and logogram data
- HMG cases and other source entries
- licence, grantor, grantee and security-interest data
- transfer data and source documents
- goods and services specifications
- current applicant/proprietor details
- agent correspondence details

The data.gov.sg datastore `_id` field is treated as provider metadata, not as an IPOS native fact. Schema-drift checks report missing or newly introduced source fields so future source changes are surfaced explicitly rather than silently ignored.

Malformed JSON families fail closed instead of being silently dropped or interpreted. Null/absent optional source fields remain absent.

Derived or interpreted legal meaning must remain separate from source-native facts.

### Neutral Native Family Changes

For an application that already exists in both snapshots, native facts are decomposed into deterministic source families such as status, journal, international data, mark data, transformation, replacement, priority, cases, licence, security interest, transfer, goods/services, applicants and agents.

A native family change records only the exact before/after source payload and changed field names. Family ordering is deterministic, application identity must match, and nested source values are preserved. This layer does not convert source phrases such as transfer, licence, case or status values into legal conclusions or dedicated semantic event types.

For identities already classified as `UPDATE_DETECTED`, the lifecycle now performs bounded-memory follow-up scans: it retains previous native facts only for the update identities, then streams the current snapshot in source order and writes neutral family-change evidence atomically. Each record carries the previous/current snapshot evidence references and the deterministic detection timestamp. This avoids retaining the full multi-gigabyte previous payload corpus in memory.

Generic delta evidence and required native-family evidence are both durable before the accepted-current pointer advances. If native evidence persistence fails, the pointer remains on the previously accepted snapshot and the just-written generic event file is removed so partially committed lifecycle evidence is not published.

A failure at any pre-pointer commit step also removes the unaccepted candidate full CSV, its candidate manifest, and candidate sidecars. The previously accepted version therefore remains the sole authoritative full snapshot instead of leaving an orphan candidate that could be mistaken for committed lifecycle state.

Pointer publication is the lifecycle commit boundary. Removal of superseded full CSVs is post-commit housekeeping: a transient filesystem lock cannot turn an already committed source version into a reported transaction failure. Failed removals are returned as `cleanup_pending_paths`, and later cycles retry all unreferenced full CSVs. Full-corpus acceptance remains stricter and fails while any snapshot cleanup is pending, preserving the one-retained-full-snapshot acceptance invariant.

Creation and deletion remain responsibilities of the generic snapshot/delta layer; family decomposition is for updates to the same source identity. A create/delete-only cycle therefore does not create an empty native-family sidecar.

## Phase 3 — Snapshot Delta and Projection

Implemented at the generic/source-observation layer:

- compare accepted previous/current observations
- deterministic fingerprints
- create/update/delete/no-change detection
- explicit jurisdiction propagation
- rejection of cross-jurisdiction or cross-identity comparisons
- durable source-observation event evidence
- deterministic neutral source-family decomposition for updated Singapore applications
- durable neutral native-family evidence with snapshot lineage
- current projection plumbing
- deterministic replay tests

This phase does not imply that every Phase 2 source family has a dedicated semantic event type.

## Storage Decision

Do not keep daily 3GB+ CSV snapshots as permanent history by default.

Durably preserve:

- source evidence manifests and provenance
- durable generic delta/event evidence
- durable neutral native-family evidence for source updates
- current projections

The operational lifecycle retains the accepted current full snapshot. Superseded full CSVs are removed after replacement and required source evidence are durably committed. A temporary cleanup failure is explicit and retryable rather than changing the accepted-current pointer; explicit audit, schema-change or evidence policy may retain/archive additional snapshots when required.

## Acceptance Gates

Source/lifecycle activation is accepted only when:

1. source metadata and hashes are reproducible;
2. identical source observations do not create false deltas;
3. fixed prior/current fixtures produce expected create/update/delete behavior;
4. replay behavior is deterministic;
5. provenance and accepted-current pointers are internally consistent;
6. changed runs retain durable generic delta evidence;
7. updated identities retain durable neutral native-family evidence before snapshot rotation;
8. failed pre-pointer runs leave no unaccepted candidate snapshot, candidate manifest or candidate sidecars;
9. post-commit snapshot cleanup failures remain observable and retryable without invalidating the committed pointer;
10. full-corpus acceptance has no pending snapshot cleanup and retains exactly one full CSV;
11. source-native facts remain separated from interpretation;
12. the acquisition adapter, lifecycle acceptance boundary and lightweight live-source probe validate the authoritative source-column contract;
13. the full-corpus lifecycle validates non-empty authoritative source data;
14. Data Engine CI remains green;
15. activation does not require rebuilding, recreating or restarting unrelated CN live workers.

## Remaining Activation Work

The next product/runtime step is to decide which neutral source-family evidence should feed projection or reviewed semantic-event consumers. Any dedicated semantic event remains a separate reviewed layer and must preserve the source evidence references rather than reinterpret raw provider changes implicitly. Recurring production scheduling, if introduced, requires its own operational acceptance and must not be inferred from the source/lifecycle acceptance described here.
