# MarkOrbit US M1.1 Ingestion

Status: LOCAL OFFICIAL-SOURCE INGESTION / HISTORICAL + DAILY PACKAGE REPLAY

US M1.1 ingests locally materialized USPTO Trademark Applications TDXF packages. Source acquisition and USPTO Open Data Portal credentials remain outside the ingestion boundary.

## Accepted package families

Historical coverage snapshot parts:

```text
apcYYYYMMDD-YYYYMMDD-NN.zip
```

Example validated against the real uploaded source:

```text
apc18840407-20251231-05.zip
```

Daily updates:

```text
apcYYMMDD.zip
apcYYMMDD.xml   # controlled validation/development only
```

Example validated against the real uploaded source:

```text
apc260108.zip
```

Historical parts use `HISTORICAL_APPLICATIONS / COVERAGE_RANGE_PART`; daily sources use `DAILY_APPLICATIONS / UPDATE_DATE`. Every historical rank is below every daily rank, so ingestion order cannot cause a historical snapshot to overwrite a later daily observation.

## Runtime flow

`run-us.ps1`:

1. applies `004_us_m1_core.sql` and the idempotent `005_us_m11_real_tdxf.sql` upgrade;
2. starts `python -m app.us.run_once` in a dedicated one-shot worker;
3. scans/registers eligible US source packages by SHA-256;
4. ingests at most one registered package in source-rank order.

CN and US use separate PostgreSQL advisory locks.

## Source integrity

The package registry records SHA-256. Before publication the resolved incoming/archive file is hashed again and must match exactly. Filename/path alone never identifies an authoritative package.

Successful packages move to `raw_data/archive/us`. Failed or missing registered sources block normal continuation until repaired by the explicit retry path.

## Streaming XML

ZIP XML members are streamed with `zipfile.ZipFile.open` directly into `xml.etree.ElementTree.iterparse`. They are not expanded into a permanent temporary corpus and are never loaded as one giant byte string.

This is important for the real sources already inspected: a historical ZIP around 61 MB expands to roughly 1.47 GB XML, while the examined daily ZIP around 30 MB expands to roughly 563 MB XML.

Completed `case-file` elements are cleared after emission. Memory therefore scales with parser state and publisher buffers rather than the full XML member.

## Real-source semantics

US M1.1 recognizes the official TDXF layout and does not depend on the earlier synthetic fixture layout. In particular:

- registration number and transaction date come from direct case children;
- publication uses `published-for-opposition-date`;
- events use `case-file-event-statement` and retain descriptions;
- owner nationality is read from the nested nationality block;
- `T/F` boolean indicators are supported;
- filed and current filing-basis flags are separate;
- Madrid facts come from the sibling `international-registration` block.

Very old historical cases can legitimately omit fields introduced later in USPTO processing. Sparse historical records are preserved rather than rejected or filled with invented values.

## Publication

Current US M1.1 core publication families remain:

- `us_case_current`
- `us_owner_current`
- `us_classification_current`
- `us_event_history`
- `us_statement_current`

Every row carries source lineage and precedence. `*_current` means latest observation for that durable record identity under source precedence; it is not a MarkOrbit legal conclusion.

The prior refinery skill exposes additional useful TDXF families (correspondent/attorney, design search, prior registrations, foreign applications, Madrid request/events, etc.). These will be introduced as additional fact tables only after their real-source identity and daily reconciliation rules are frozen.

## Failure and retry

Retry remains deterministic full-package replay:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\retry-us.ps1
```

Before replay, all rows carrying the failed/interrupted package UUID are synchronously removed, then the authoritative package is parsed again from the beginning. No XML-internal checkpoint is maintained.

## Local sequence

For an initial historical build, place all historical coverage parts under:

```text
raw_data\incoming\us\
```

Then repeatedly execute:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-us.ps1
```

The source-rank contract processes historical parts before daily updates even if files were copied in a different filesystem order. After the historical baseline is complete, continue placing daily `apcYYMMDD.zip` sources in the same incoming directory and use the same one-shot command.

Before large real replay, run the live fixture gate:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-us-m1-fixture.ps1
```
