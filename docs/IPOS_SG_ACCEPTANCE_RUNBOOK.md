# Singapore IPOS Acceptance Runbook

## Scope

This runbook separates lightweight live-source contract acceptance from the multi-GB full-corpus acceptance. Passing the live probe must not be described as a completed full-corpus ingestion.

Dataset contract:

- source: Singapore IPOS Trademark Applications
- dataset id: `d_6145acb2130bf781165258e76a584383`
- snapshot file: `IPOSTradeMarkApplications.csv`
- model: current authoritative snapshot plus durable delta evidence; do not retain daily full snapshots

## Level 1 — Live source contract

Run:

```bash
python -m app.snapshot_delta.ipos_sg_acceptance
```

Optional download-materialization check without downloading the CSV:

```bash
python -m app.snapshot_delta.ipos_sg_acceptance --resolve-download-url
```

Acceptance requires:

- the official datastore endpoint is reachable;
- the dataset reports at least one row;
- `applicationNumber` and `markStatus` are present;
- one real sample application identity is returned;
- when requested, initiate/poll resolves an official signed download URL.

The GitHub workflow `Singapore IPOS Live Source Acceptance` stores the JSON result as a short-lived artifact.

## Level 2 — Full corpus acceptance

Run on an operator host with sufficient disk and network capacity:

```bash
python -m app.snapshot_delta.ipos_sg_full_acceptance \
  --state-dir /path/to/ipos-sg-state \
  --report-path /path/to/ipos-sg-full-corpus-acceptance.json
```

`DATA_GOV_SG_API_KEY` is optional and, when provided, is used only for data.gov.sg API calls. It is not forwarded to the signed object-storage download URL.

The guarded workflow `Singapore IPOS Full Corpus Acceptance` is manual-only and requires explicit confirmation because it downloads the complete multi-GB source. It uploads only the compact JSON acceptance report, never the full CSV.

The full-corpus acceptance report proves:

- complete snapshot download byte count;
- source row count;
- schema hash and content SHA-256;
- elapsed acquisition/lifecycle time;
- current snapshot size and storage reference;
- exactly one retained full CSV after commit;
- delta event count and evidence path when the corpus changed.

## Lifecycle acceptance

First successful corpus:

- expected status: `BOOTSTRAPPED`;
- no synthetic CREATE flood is emitted;
- exactly one current snapshot is retained.

Identical subsequent corpus:

- expected status: `UNCHANGED`;
- no new delta event file is produced;
- the existing authoritative current version remains active.

Changed subsequent corpus:

- expected status: `CHANGED`;
- CREATE/UPDATE/DELETE events are durably written before current advances;
- the previous full CSV is discarded only after successful event generation and pointer publication;
- exactly one full CSV remains afterward.

## Evidence rule

Do not mark Singapore IPOS full-corpus activation complete from unit tests, static CI, or the Level 1 probe alone. A Level 2 report from a completed official full-corpus run is required for that claim.
