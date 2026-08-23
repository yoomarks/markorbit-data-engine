# Singapore IPOS Activation Record

## Status

Source/lifecycle activation is accepted on the real public corpus. Generic snapshot/delta behavior and current projection plumbing are implemented. The first field-level native fact extraction slice is implemented for selected authoritative IPOS fields; broader semantic-family coverage remains follow-on work.

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

## Phase 2 — Native Semantic Extraction

Foundation implemented for selected source-native fields. This remains separate from source/lifecycle acceptance and does not introduce legal interpretation.

The extractor requires authoritative application identity and mark status, preserves source date values as source strings, and parses the source JSON-array families without changing their nested source structure. It accepts both official API field names and the corresponding CSV display headings.

The first slice covers:

- application number and filing date
- application/trade mark type
- mark status and source status/update dates
- registration completion, expiry, publication and last-modified dates
- international-registration details
- mark data
- goods and services specifications
- priority claims
- current applicant/proprietor details
- agent correspondence details
- transfer and licence data
- source documents

Malformed JSON families fail closed instead of being silently dropped or interpreted. Null/absent optional source fields remain absent.

Broader native semantic-family extraction may be added where the authoritative source provides it, including additional journal, case, security-interest, transformation/replacement and related source families.

Derived or interpreted legal meaning must remain separate from source-native facts.

## Phase 3 — Snapshot Delta and Projection

Implemented at the generic/source-observation layer:

- compare accepted previous/current observations
- deterministic fingerprints
- create/update/delete/no-change detection
- explicit jurisdiction propagation
- rejection of cross-jurisdiction or cross-identity comparisons
- durable source-observation event evidence
- current projection plumbing
- deterministic replay tests

This phase does not imply that every Phase 2 source family has a dedicated semantic event type.

## Storage Decision

Do not keep daily 3GB+ CSV snapshots as permanent history by default.

Durably preserve:

- source evidence manifests and provenance
- durable delta/event evidence
- current projections

The operational lifecycle retains the accepted current full snapshot. Older full CSVs may leave the hot lifecycle after replacement is durably committed; explicit audit, schema-change or evidence policy may retain/archive additional snapshots when required.

## Acceptance Gates

Source/lifecycle activation is accepted only when:

1. source metadata and hashes are reproducible;
2. identical source observations do not create false deltas;
3. fixed prior/current fixtures produce expected create/update/delete behavior;
4. replay behavior is deterministic;
5. provenance and accepted-current pointers are internally consistent;
6. changed runs retain durable delta evidence;
7. source-native facts remain separated from interpretation;
8. the full-corpus lifecycle validates non-empty authoritative source data;
9. Data Engine CI remains green;
10. activation does not require rebuilding, recreating or restarting unrelated CN live workers.

## Remaining Activation Work

Continue Phase 2 by adding source-backed extraction/tests for additional authoritative families only when product/runtime consumers need them. Dedicated semantic event families should be introduced separately from raw fact extraction. Recurring production scheduling, if introduced, requires its own operational acceptance and must not be inferred from the source/lifecycle acceptance described here.
