# TMclass fact store

Knowledge owns TMclass acquisition and immutable RawArtifact lineage. Data Engine admits only
validated `TMCLASS_SOURCE_EVIDENCE_V1` packages and stores normalized source facts in the `tmclass`
PostgreSQL schema.

Migrate the component before first admission:

```bash
python -m app.tmclass.cli migrate
```

Normal service-to-service admission uses the authenticated endpoint:

```text
POST /api/admin/v2/fact-admissions/tmclass/evidence
```

For a local governed backfill produced by the Knowledge HAR importer, the same contract and
repository admission path can consume the complete bundle:

```bash
python -m app.tmclass.import_cli /evidence/tmclass-source-evidence-bundle.json
```

The importer validates the bundle identity and count before admitting its individual evidence
packages. Replays are idempotent by Knowledge `rawArtifactId`; changed source bytes must arrive as a
new immutable RawArtifact version.

For a resumable live-corpus backfill, watch the Knowledge bundle directory and write hash-bound
receipts outside it:

```bash
python -m app.tmclass.import_corpus_cli /evidence/tmclass-bundles \
  --receipt-root /data/control/tmclass-import-receipts \
  --capture-complete /evidence/tmclass-live/COMPLETE.json \
  --continuous
```

Only a receipt whose bundle SHA-256 still matches is skipped. A changed bundle behind an existing
receipt fails closed; an interrupted admission can be replayed because individual Knowledge
evidence identities are idempotent.
