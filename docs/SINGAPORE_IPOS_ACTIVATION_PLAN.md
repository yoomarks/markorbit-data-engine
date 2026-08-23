# Singapore IPOS Activation Plan

## Source

Authority: Intellectual Property Office of Singapore (IPOS)

Dataset:

`d_6145acb2130bf781165258e76a584383`

File:

`IPOSTradeMarkApplications.csv`

Source type:

`CURRENT_SNAPSHOT`

## Phase 1

Implement source adapter:

- dataset metadata registry
- schema capture
- snapshot manifest generation
- download/API strategy

## Phase 2

Implement native extraction:

- application identity
- mark data
- applicant/proprietor
- goods and services
- status observations
- agent correspondence
- Madrid related observations
- transfer/license observations

## Phase 3

Implement delta activation:

- compare consecutive snapshots
- emit observation change events
- rebuild current projection
- deterministic replay acceptance

## Storage Decision

Do not store daily 3GB+ CSV files as permanent history.

Store:

- source evidence manifests
- controlled snapshots
- durable deltas
- current projections

## Acceptance Gate

Singapore is accepted only after:

- source replay is deterministic
- identical snapshot produces identical projection
- delta fixtures produce expected events
- native facts remain separated from interpretation
