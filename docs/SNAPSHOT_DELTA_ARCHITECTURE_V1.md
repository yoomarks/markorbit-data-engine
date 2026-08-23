# Snapshot Delta Architecture V1

## Purpose

Define the provider-side architecture for jurisdictions whose authoritative source is a current-state snapshot rather than an event feed.

Singapore IPOS is the first activated source using this model.

## Design Principle

Data Engine does not use daily full source files as the durable history model.

The source snapshot is evidence. Durable source history is represented by manifests, provenance and detected changes.

Pipeline:

```
Official Snapshot
      |
      v
Snapshot Manifest
      |
      v
Content + Row Fingerprint
      |
      v
Delta Detection
      |
      v
Native Change Event Store
      |
      v
Current Projection
```

## Storage Rules

### Raw Snapshot Evidence

The operational lifecycle retains the accepted current full snapshot and its manifest. Manifests, provenance and durable delta evidence remain queryable after a prior full CSV leaves the hot lifecycle.

Additional full snapshots may be retained or archived when an explicit audit, schema-change or evidence policy requires them. Daily identical full CSV files are not permanent history by default.

The lifecycle MUST NOT delete the only accepted current snapshot before a replacement snapshot and its manifest are durably committed.

### Fingerprints

A snapshot record fingerprint MUST be deterministic and source-backed.

Fingerprint inputs are jurisdiction-specific but normally include:

- source identity
- application identity
- status observations
- party observations
- goods/services observations
- mark observations
- rights-management observations when present

### Delta Events

Delta events describe source observation changes, not legal conclusions.

Initial event vocabulary:

- CREATE_DETECTED
- UPDATE_DETECTED
- STATUS_CHANGED
- PARTY_CHANGED
- GOODS_CHANGED
- TRANSFER_OBSERVED
- LICENSE_OBSERVED
- DELETE_DETECTED

Generic snapshot/delta primitives MUST receive jurisdiction explicitly. Provider-neutral code MUST NOT silently default observations to Singapore or require data.gov.sg-specific acquisition fields.

## Jurisdiction Strategy

Data Engine supports three source models:

1. Event-first source
2. Snapshot-first source
3. Hybrid source

Singapore IPOS is classified as Snapshot-first.

## Singapore Activation Boundary

The Singapore source/lifecycle path is implemented as:

1. acquire the latest IPOS current-state snapshot through the Singapore provider adapter;
2. build an auditable `SnapshotManifest` with source identity, retrieval time, schema hash, content hash, row count and storage reference;
3. validate source-native critical schema needed for application observations;
4. compare the accepted previous/current observations through the generic snapshot/delta engine;
5. emit source-observation deltas when a source-backed record changes;
6. rebuild/update the current projection through the snapshot lifecycle;
7. retain the accepted current full snapshot and durable manifest/evidence according to lifecycle policy.

The Singapore adapter MUST preserve source-native identity and MUST NOT bypass the snapshot/delta layer with direct CSV-to-current writes.

The accepted full-corpus runtime proves source acquisition, manifesting, lifecycle commit and evidence invariants on the real public corpus. It does **not** by itself declare field-level native semantic extraction complete, and it does **not** imply that a recurring production schedule is active.

Normalization or semantic extraction rules added after source activation MUST remain explicit, auditable and separate from legal interpretation.
