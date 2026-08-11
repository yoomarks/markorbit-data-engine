# USPTO TTAB Full-Corpus Replay

This workflow turns TTAB M1.2 into an explicit deterministic full-bulk-corpus operation without changing the procedural-fact model.

## Source contract

The replay authority is a JSON manifest under `RAW_DATA_PATH`, normally:

`manifests/us_ttab/corpus.json`

Example:

```json
{
  "manifest_version": "US_TTAB_CORPUS_MANIFEST_V1",
  "expected_historical_packages": 1,
  "expected_daily_packages": 2,
  "daily_through": "2026-08-09",
  "sources": [
    {
      "path": "incoming/us_ttab/historical.zip",
      "source_kind": "TTAB_BULK_HISTORICAL_XML",
      "snapshot_at": "2026-05-13T12:00:00Z"
    },
    {
      "path": "incoming/us_ttab/tt260808.zip",
      "source_kind": "TTAB_BULK_DAILY_XML",
      "snapshot_at": "2026-08-08T12:00:00Z"
    },
    {
      "path": "incoming/us_ttab/tt260809.zip",
      "source_kind": "TTAB_BULK_DAILY_XML",
      "snapshot_at": "2026-08-09T12:00:00Z"
    }
  ]
}
```

The timestamps above are examples only. `snapshot_at` must be populated from authoritative source metadata and is never inferred from a filename.

V1 accepts only official bulk source kinds: one `TTAB_BULK_HISTORICAL_XML` baseline plus zero or more `TTAB_BULK_DAILY_XML` sources. Per-proceeding `TTABVUE_PROCEEDING_RAWXML_SNAPSHOT` captures remain valid for their existing purpose but are intentionally excluded from a formal full-corpus manifest.

Every daily snapshot must be later than the historical baseline. Duplicate snapshot timestamps fail closed because same-millisecond revision/multipart precedence is not modeled. Daily calendar gaps are not guessed; completeness is pinned by the explicit expected daily package count and `daily_through` assertion.

Preflight hashes every source, validates ZIP/XML structure, and remains read-only. After successful ingestion moves an incoming source to `archive/us_ttab`, the same manifest path resolves the archived copy without copying or mutating raw data.

## Commands

Read-only preflight:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\preflight-us-ttab-corpus.ps1
```

Dry-run deterministic plan:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\replay-us-ttab-deterministic.ps1 -All
```

Apply all remaining packages:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\replay-us-ttab-deterministic.ps1 -Apply -All
```

A failed/interrupted/missing package is a strict barrier and needs explicit retry authorization:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\replay-us-ttab-deterministic.ps1 -Apply -All -ResumeFailed
```

Final manifest-aware acceptance:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit-us-ttab-corpus.ps1
```

`PASS_WITH_WARNINGS` can be a valid TTAB corpus acceptance state when the underlying M1.2 audit reports coverage warnings such as TTAB property serials not yet present in the Application current projection. It is not a waiver for source, lineage, orphan, duplicate, registry, or replay-completeness failures.

## Safety invariants

- dry-run is default;
- persistent worker must be stopped;
- scripts build the current one-shot worker image before corpus commands;
- official archives remain compressed; XML is streamed during ingestion;
- no filename-derived `snapshot_at`;
- no same-millisecond revision precedence guessing;
- successful packages must form a strict manifest prefix;
- any US_TTAB registry package outside the manifest blocks formal corpus replay;
- retry remains full-package cleanup and replay;
- final acceptance requires the exact manifest SHA set to be registered and successful;
- TTAB codes and procedural observations are preserved as facts;
- no deadline-validity inference;
- no legal-outcome conclusion;
- no substantive-rights conclusion.
