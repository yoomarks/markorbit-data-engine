# US Assignment M1.0 — Recorded Assignment Facts

Status: OFFICIAL-FACT FOUNDATION / NO LEGAL TITLE CONCLUSION

US Assignment M1.0 ingests USPTO trademark assignment/recordation XML as a data family that is deliberately isolated from the US application case-file replay pipeline.

## Source semantics

USPTO assignment records describe recorded documents and claimed interests. A recorded assignment can include assignments, mergers, name changes, security interests, licenses and other conveyance text. Recordation is a public-record function; MarkOrbit therefore does **not** treat a recorded document as proof that the transaction is legally valid or that the named assignee is the legal current owner.

Every API response carries:

```text
USPTO_RECORDED_ASSIGNMENT_FACTS_NOT_LEGAL_TITLE_CONCLUSION
legal_ownership_conclusion=false
```

## Official record structure modeled

The M1.0 model follows the USPTO Trademark Assignment dataset structure:

- assignment record keyed by reel/frame
- one-to-many assignors
- one-to-many assignees
- one-to-many trademark properties/document IDs

Preserved fields include reel/frame, recorded date, last update date, page count, conveyance text, purge indicator, correspondent, assignor/assignee names and addresses, execution/acknowledgement dates, serial number, registration number and international registration number.

Raw date text is retained. A typed date is created only for a complete valid date; partial/invalid dates are never repaired by inventing a day.

## Isolation from US application replay

Assignment packages use:

```text
jurisdiction = US_ASSIGNMENT
schema_version = US_ASSIGNMENT_M1.0
lock = markorbit:us:assignment-ingestion
incoming = raw_data/incoming/us_assignment
archive = raw_data/archive/us_assignment
```

The application replay/reset/acceptance pipeline filters `jurisdiction='US'`, so Assignment packages cannot enter the US M1.4 history→daily ordering by accident.

## Source registration is explicit

M1.0 does not guess assignment package precedence from a filename. Register a locally materialized XML/ZIP with an explicit source effective date and source kind:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\register-us-assignment.ps1 `
  -Path .\raw_data\incoming\us_assignment\assignment.xml `
  -EffectiveDate 2026-08-09 `
  -SourceKind DAILY_ASSIGNMENT_XML
```

Supported kinds:

- `DAILY_ASSIGNMENT_XML`
- `ASSIGNMENT_SNAPSHOT_XML`

A different SHA-256 for the same source kind/effective date is rejected because same-day revision precedence is not yet modeled. A SHA already registered under another jurisdiction is also rejected rather than being relabeled.

Then ingest one package:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-us-assignment.ps1
```

Retry a failed/interrupted package:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\retry-us-assignment.ps1
```

## Durable tables

All four Assignment tables are append-only `MergeTree` history tables:

- `us_assignment_record_history`
- `us_assignment_assignor_history`
- `us_assignment_assignee_history`
- `us_assignment_property_history`

A later source package may update an old reel/frame. The newer observation does not delete the earlier observation. Read APIs project the newest reel/frame observation by deterministic `source_rank` and then select parties/properties from that exact `source_package_id`.

This prevents an old property link from surviving a later corrected record merely because history is append-only.

## Read-only APIs

```text
GET /api/us/assignments/reel-frame/{reel_no}/{frame_no}
GET /api/us/assignments/{serial_number}
GET /api/us/assignments/{serial_number}/reconciliation
```

The reconciliation endpoint compares the current case-file owner names with the latest recorded-assignment assignee names using only whitespace/case-normalized exact name sets. Its result is one of:

- `MATCH`
- `DIFFER`
- `NOT_COMPARABLE`

It does not perform entity resolution and does not determine legal title.

## Failure/retry semantics

Before publishing, the registered source SHA-256 is recomputed. Duplicate reel/frame entries inside one source package hard-fail. On failure or retry, all rows from that package UUID are synchronously removed from the four Assignment tables before replay.

Malformed property serial numbers are preserved as raw facts and counted in the package profile rather than silently corrected or dropped.

## Validation

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-us-assignment-fixture.ps1
```

The live fixture runs against PostgreSQL + ClickHouse and publishes two source-ranked observations of the same reel/frame. It verifies:

- append-only record/assignor/assignee/property history
- latest reel/frame projection comes from the newer package
- serial lookup uses only properties from the latest reel/frame observation
- newer assignee projection wins without deleting older observations
- package-scoped cleanup leaves zero residual fixture history
- no legal ownership conclusion is emitted

## Deferred work

M1.0 intentionally does not include:

- authenticated USPTO Open Data Portal downloader
- guessed current owner/title chain
- validity analysis of the recorded conveyance
- entity-resolution fuzzy matching
- assignment document-image retrieval
- same-effective-date revision semantics

Those require separate evidence/rule decisions and should not be mixed into the official-fact ingestion layer.
