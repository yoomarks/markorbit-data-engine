# Delta Event Model V1

## Purpose

Represent source observation changes for snapshot-first jurisdictions.

Delta events describe observed source changes, not legal conclusions.

## Initial Event Vocabulary

- CREATE_DETECTED
- UPDATE_DETECTED
- STATUS_CHANGED
- PARTY_CHANGED
- GOODS_CHANGED
- TRANSFER_OBSERVED
- LICENSE_OBSERVED
- DELETE_DETECTED

## Boundary

Example:

PARTY_CHANGED means the authoritative snapshot shows a changed party observation.

It does not automatically mean a legally effective transfer has completed.

## Requirements

- Events must reference source evidence.
- Replay from the same snapshots must be deterministic.
- Current projections are rebuilt from native observations and events.
