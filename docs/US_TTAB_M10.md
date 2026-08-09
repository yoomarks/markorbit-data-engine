# US TTAB M1.0

## Scope

`US_TTAB_M1.0` is the isolated USPTO Trademark Trial and Appeal Board procedural-fact subsystem.

It stores materialized TTABVUE proceeding snapshots as append-only source observations. It does **not** determine who won, whether a party owns enforceable trademark rights, whether a TTAB-observed due date remains legally operative, or whether an infringement claim exists.

Semantic marker:

`USPTO_TTAB_PROCEDURAL_FACTS_NOT_OUTCOME_OR_SUBSTANTIVE_RIGHTS_CONCLUSION`

## Official source contract

The source contract is based on current USPTO TTABVUE public proceeding data and the TTABVUE help-described raw XML view. Modeled official fact families are:

- proceeding number and type
- filing date
- proceeding status text and status date
- Board contact/staff fields when present
- plaintiff/defendant/applicant/other party names
- correspondence contact fields when present
- related trademark serial number, registration number, mark text and application/property status
- prosecution-history/docket rows
- filing/history date
- history text
- TTABVUE due-date value when supplied
- document URL when supplied

A due date is stored as a **due-date observation**. M1.0 does not infer that the date remains current, was extended, was suspended, was satisfied, or is otherwise legally operative.

## Real-source validation status

The engine has a robust alias-based XML parser and a TTABVUE-shaped synthetic fixture grounded in the official fields above. The repository does **not yet claim exact production raw-XML tag compatibility**, because an actual TTABVUE `rawxml=1` payload has not been materialized into this development environment.

Before marking production TTAB source compatibility as validated, materialize at least one authoritative TTABVUE raw XML file locally and run it through registration, ingestion and source-backed acceptance.

## Isolation

- jurisdiction: `US_TTAB`
- schema: `US_TTAB_M1.0`
- advisory lock: `markorbit:us:ttab-ingestion`
- incoming: `raw_data/incoming/us_ttab`
- archive: `raw_data/archive/us_ttab`
- schema file: `database/clickhouse/init/010_us_ttab_m10.sql`

TTAB tables are intentionally excluded from US application M1.4 replay/reset and from US Assignment M1.0 ingestion/reset behavior.

## Durable tables

1. `us_ttab_proceeding_history`
2. `us_ttab_party_history`
3. `us_ttab_property_history`
4. `us_ttab_docket_history`

All four are append-only `MergeTree` histories keyed by source package and deterministic observation keys.

## Snapshot precedence

Operators must provide a timezone-aware snapshot timestamp. The engine does not guess precedence from filenames.

Source rank is ordered by:

1. UTC snapshot timestamp
2. package sequence

The same SHA-256 cannot later be relabeled with different TTAB source-kind or snapshot metadata.

## Registration and ingestion

Apply schema:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\apply-us-ttab-schema.ps1
```

Register an already-materialized TTABVUE XML/ZIP:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\register-us-ttab.ps1 `
  -Path .\raw_data\incoming\us_ttab\92081234.xml `
  -SnapshotAt "2026-08-09T12:00:00Z"
```

Process one package:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-us-ttab.ps1
```

Retry one failed/interrupted package:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\retry-us-ttab.ps1
```

The ingest path verifies the registered SHA-256, hard-fails duplicate proceeding numbers inside one package, synchronously removes package outputs before retry, and archives successful source material.

## Read-only API

- `GET /api/us/ttab/schema`
- `GET /api/us/ttab/acceptance`
- `GET /api/us/ttab/readiness`
- `GET /api/us/ttab/by-serial/{serial_number}`
- `GET /api/us/ttab/timeline/{proceeding_number}`
- `GET /api/us/ttab/proceedings/{proceeding_number}`

The latest-proceeding API projects only the newest source-ranked snapshot. Historical observations remain durable.

## Derived snapshot differences

The timeline may report evidence differences such as:

- `STATUS_TEXT_CHANGED`
- `STATUS_DATE_CHANGED`
- `BOARD_STAFF_CHANGED`
- `PARTY_SET_CHANGED`
- `PROPERTY_SET_CHANGED`
- `DOCKET_ENTRY_ADDED`
- `DOCKET_ENTRY_REMOVED_FROM_SNAPSHOT`
- `DOCKET_DUE_DATE_OBSERVATION_CHANGED`
- `DOCKET_ENTRY_CONTENT_CHANGED`

These are comparisons between source snapshots, not legal interpretations.

## Acceptance and readiness

Source-backed audit:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-us-ttab-real-data.ps1 -VerifySourceFiles
```

Readiness:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-us-ttab-readiness.ps1 -VerifySourceFiles
```

Acceptance checks include:

- schema readiness
- package state
- observation-key uniqueness
- child snapshot → proceeding snapshot integrity
- source package/rank lineage
- unique latest proceeding projection
- malformed related serial-number coverage warnings
- related serial coverage against `us_case_current`
- optional local authoritative source SHA-256 evidence

Malformed serials and missing US-case cross-links are data-quality/coverage warnings, not proof that TTAB data is invalid.

## Live fixture

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-us-ttab-fixture.ps1
```

The fixture creates two source-ranked TTAB snapshots for the same synthetic cancellation proceeding and checks:

- append-only history
- newest snapshot projection
- TTAB serial → US case cross-link
- status transition
- new docket entry
- changed due-date observation
- source-backed acceptance
- readiness
- cleanup to zero fixture residue

The fixture still uses repository-controlled synthetic TTABVUE-shaped XML; it is not evidence that a real raw TTABVUE payload has already been parsed successfully.
