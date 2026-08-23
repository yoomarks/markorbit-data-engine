# Snapshot Delta Architecture V1

## Purpose

Define the provider-side architecture for jurisdictions whose authoritative source is a current-state snapshot rather than an event feed.

Singapore IPOS is the first activation target.

## Design Principle

Data Engine does not archive daily full source files as history.

The source snapshot is evidence. The durable history is the detected change.

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

Retain:

- genesis snapshot
- periodic audit snapshots
- schema-change snapshots
- explicitly requested evidence snapshots

Do not retain daily identical snapshots as primary history.

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

## Jurisdiction Strategy

Data Engine supports three source models:

1. Event-first source
2. Snapshot-first source
3. Hybrid source

Singapore IPOS is classified as Snapshot-first.

## Singapore Activation

Future SG adapter MUST implement:

- source manifest
- snapshot fingerprint
- deterministic replay
- native observations
- delta events
- current projection

It MUST NOT bypass the snapshot/delta layer with direct CSV-to-current writes.
