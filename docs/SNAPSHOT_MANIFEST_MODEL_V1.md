# Snapshot Manifest Model V1

## Purpose

Define the evidence identity of current-state jurisdiction sources.

A snapshot manifest records what was observed from an authoritative source without treating the source file itself as durable history.

## Manifest Fields

Required:

- jurisdiction
- source_id
- dataset_id
- retrieved_at
- source_uri
- schema_hash
- content_hash
- row_count
- storage_reference

## Lifecycle

```
Source Snapshot
      |
      v
Manifest
      |
      v
Fingerprint
      |
      v
Delta Detection
      |
      v
Change Events
      |
      v
Current Projection
```

## Rules

- Identical snapshots must produce identical manifests.
- Schema changes must create explicit evidence.
- Snapshot evidence is retained according to source policy.
- Change history is represented by detected observation deltas, not duplicate source archives.
