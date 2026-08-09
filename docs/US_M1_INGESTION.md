# MarkOrbit US M1 Ingestion

Status: LOCAL OFFICIAL-SOURCE INGESTION / PACKAGE REPLAY

US M1 ingests USPTO Trademark Daily Applications XML that has already been materialized under
`raw_data/incoming/us`. Source acquisition and USPTO Open Data Portal credentials remain outside
the ingestion boundary.

## Runtime flow

`run-us.ps1` performs two steps:

1. idempotently applies `database/clickhouse/init/004_us_m1_core.sql`;
2. runs `python -m app.us.run_once` in a dedicated one-shot worker container.

Each cycle scans/registers eligible incoming US packages and ingests at most one registered
package in source-rank order. CN and US use separate PostgreSQL advisory locks, so the US one-shot
path does not reuse or weaken the guarded CN ingestion lock.

## Accepted source package

US M1 currently accepts:

- `apcYYMMDD.zip` containing one or more XML members;
- extracted `apcYYMMDD.xml` for controlled validation/development.

Unknown filename precedence is rejected. Multiple different sources for the same update date are
also rejected. If an update date already exists in the registry, a different SHA-256 is treated as
an unmodeled revision and blocked rather than silently outranking the registered source.

## Source integrity

Package registration records the authoritative SHA-256 in PostgreSQL. Before publication the
worker hashes the resolved incoming/archive file again and requires an exact match. A basename or
filesystem path alone is never sufficient to select a source package.

Successfully ingested packages move to `raw_data/archive/us`. If an identical authoritative copy
already exists there, the incoming duplicate is removed. A same-name different-content file is
kept under a hash-suffixed archive filename; however, same-update-date revision semantics still
require an explicit future policy before that new source may be registered.

## XML processing

ZIP members are streamed directly through `zipfile.ZipFile.open` into the standard-library XML
`iterparse` parser. XML is not extracted to a temporary corpus on disk. Completed case elements
are cleared as they are emitted, so memory use is bounded by parser state and publisher batches
rather than the full daily XML document.

A source package must produce at least one valid eight-digit serial number. Duplicate serial
numbers within a single source package fail closed because US M1 has not defined an intra-package
revision rule for two complete observations of the same case.

## Publication

The publisher writes deterministic identities and source lineage to:

- `us_case_current`
- `us_owner_current`
- `us_classification_current`
- `us_event_history`
- `us_statement_current`

Every row carries the source rank and source package UUID. Record hashes are canonical SHA-256
hashes of the parsed official fact record. The case UUID is a deterministic UUID5 derived from the
USPTO serial number.

`*_current` means latest durable observation for that record identity under source precedence. It
does **not** mean that MarkOrbit has already determined the legal current owner, live/dead legal
status, or current enforceable goods scope. Those legal/product interpretations are separate,
versioned models.

## Failure and retry

An interrupted US ingestion is converted from `PROCESSING` to `INTERRUPTED` after the US advisory
lock is reclaimed. Retry is deterministic full-package replay:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\retry-us.ps1
```

Before a retry, all ClickHouse rows whose source package UUID equals the failed/interrupted package
are synchronously deleted, then the authoritative source file is parsed again from the beginning.
There is no XML-internal checkpoint state to validate or resume.

If any US package is `FAILED` or `MISSING_FILE`, normal `run-us.ps1` continuation is blocked until
the explicit retry path repairs the failed source. This prevents advancing to later daily updates
while an earlier registered update is unresolved.

## First local run

Place one official daily package under:

```text
raw_data/incoming/us/apcYYMMDD.zip
```

Then run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-us.ps1
```

Run the command again to process the next registered package. US M1 intentionally remains
one-package-at-a-time until real USPTO package acceptance has established runtime and data-quality
baselines.
