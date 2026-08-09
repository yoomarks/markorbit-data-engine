# MarkOrbit US M1.2 Ingestion

Status: LOCAL OFFICIAL-SOURCE INGESTION / HISTORICAL + DAILY PACKAGE REPLAY

US M1.2 ingests locally materialized USPTO Trademark Applications TDXF packages. Source acquisition and USPTO Open Data Portal credentials remain outside the ingestion boundary.

## Accepted package families

Historical coverage snapshot parts:

```text
apcYYYYMMDD-YYYYMMDD-NN.zip
```

Validated real example:

```text
apc18840407-20251231-05.zip
```

Daily updates:

```text
apcYYMMDD.zip
apcYYMMDD.xml   # controlled validation/development only
```

Validated real example:

```text
apc260108.zip
```

Historical parts use `HISTORICAL_APPLICATIONS / COVERAGE_RANGE_PART`; daily sources use `DAILY_APPLICATIONS / UPDATE_DATE`. Every historical rank is below every daily rank, so ingestion order cannot cause a historical snapshot to overwrite a later daily observation.

## Runtime flow

`run-us.ps1`:

1. applies `004_us_m1_core.sql`, `005_us_m11_real_tdxf.sql`, and `006_us_m12_snapshot_semantics.sql`;
2. starts `python -m app.us.run_once` in a dedicated one-shot worker;
3. scans/registers eligible US source packages by SHA-256;
4. ingests at most one registered package in source-rank order.

CN and US use separate PostgreSQL advisory locks.

## Source integrity

The package registry records SHA-256. Before publication the resolved incoming/archive file is hashed again and must match exactly. Filename/path alone never identifies an authoritative package.

Successful packages move to `raw_data/archive/us`. Failed or missing registered sources block normal continuation until repaired by the explicit retry path.

## Streaming XML

ZIP XML members are streamed with `zipfile.ZipFile.open` directly into `xml.etree.ElementTree.iterparse`. They are not expanded into a permanent temporary corpus and are never loaded as one giant byte string.

The inspected historical ZIP is about 61 MB compressed and roughly 1.47 GB expanded XML; the inspected daily ZIP is about 30 MB compressed and roughly 563 MB expanded XML. Completed `case-file` elements are cleared after emission, so memory scales with parser state and publisher buffers rather than the full XML member.

## Real-source field semantics

The parser recognizes the real TDXF layout:

- registration number and transaction date from direct case children;
- publication from `published-for-opposition-date`;
- events from `case-file-event-statement`, retaining descriptions;
- nested owner nationality;
- `T/F` boolean indicators;
- filed/current filing-basis flags as separate facts;
- Madrid facts from sibling `international-registration`.

Very old historical cases may legitimately omit fields introduced later in USPTO processing. Sparse historical records are preserved rather than rejected or filled with invented values.

## M1.2 child snapshot replacement

For every touched serial number, the new TDXF case observation is authoritative for these replaceable child families:

- `us_owner_current`
- `us_classification_current`
- `us_statement_current`

Before a buffered batch is written, the publisher reads older active child identities for the touched serials. Any older identity that is absent from the new snapshot receives a tombstone at the new source rank. This means a prior owner, class row, or statement cannot remain falsely current merely because the later snapshot omitted it.

The query only considers rows with `source_rank < new_source_rank`, so a late historical replay cannot retire a newer daily fact.

`us_event_history` is deliberately different: events remain cumulative evidence. A later snapshot does not tombstone an older event simply because the event is no longer repeated.

Each omission tombstone carries the current package UUID and source lineage. Package metrics expose `snapshot_tombstone_counts` for owner/classification/statement.

## Failure and retry

Retry remains deterministic full-package replay:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\retry-us.ps1
```

Before replay, all rows carrying the failed/interrupted package UUID are synchronously removed, including M1.2 omission tombstones. Removing a failed package therefore reveals the prior valid snapshot again before replay starts. No XML-internal checkpoint is maintained.

## Local validation

Run both live database gates before a larger replay:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-us-m1-fixture.ps1
```

That command runs:

1. the US M1.1 real-TDXF regression fixture;
2. the US M1.2 historical→daily child snapshot fixture.

The M1.2 fixture verifies that an owner, classification, and statement present in an older snapshot disappear from current state when omitted by a newer snapshot, while both old and new events remain in event history.

## Local replay sequence

Place historical coverage parts and subsequent daily packages under:

```text
raw_data\incoming\us\
```

Then repeatedly execute:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-us.ps1
```

The source-rank contract processes historical parts before daily updates even if filesystem order differs. After the historical baseline is complete, continue adding daily `apcYYMMDD.zip` packages and use the same one-shot command.
