# USPTO Assignment Full-Corpus Replay

This workflow turns the isolated Assignment M1.0 one-package runner into an explicit, deterministic full-corpus operation without changing Assignment fact semantics.

## Source contract

The replay authority is a JSON manifest stored under `RAW_DATA_PATH`, normally:

`manifests/us_assignment/corpus.json`

Example:

```json
{
  "manifest_version": "US_ASSIGNMENT_CORPUS_MANIFEST_V1",
  "expected_snapshot_packages": 1,
  "expected_daily_packages": 2,
  "daily_through": "2026-08-09",
  "sources": [
    {
      "path": "incoming/us_assignment/historical.zip",
      "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
      "effective_date": "2026-05-13"
    },
    {
      "path": "incoming/us_assignment/daily-1.zip",
      "source_kind": "DAILY_ASSIGNMENT_XML",
      "effective_date": "2026-05-14"
    },
    {
      "path": "incoming/us_assignment/daily-2.zip",
      "source_kind": "DAILY_ASSIGNMENT_XML",
      "effective_date": "2026-08-09"
    }
  ]
}
```

`effective_date` is mandatory source metadata. It is never inferred from the filename. V1 requires one authoritative historical snapshot. Every daily effective date must be later than that snapshot. Same-effective-date revisions are not modeled and fail closed. Daily calendar gaps are not guessed; completeness is pinned by the explicit expected package count and `daily_through` assertion.

The preflight hashes every declared source and verifies that ZIPs contain XML. It is read-only. A manifest path remains stable after successful ingestion: if an `incoming/us_assignment/...` source has moved to `archive/us_assignment/...`, preflight resolves the archived copy by the same Assignment-domain basename.

## Commands

Read-only source preflight:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\preflight-us-assignment-corpus.ps1
```

Dry-run deterministic registry/replay plan:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\replay-us-assignment-deterministic.ps1 -All
```

Apply the complete remaining strict-prefix plan:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\replay-us-assignment-deterministic.ps1 -Apply -All
```

A failed/interrupted package is a barrier. Retry is explicit:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\replay-us-assignment-deterministic.ps1 -Apply -All -ResumeFailed
```

Final manifest-aware acceptance:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit-us-assignment-corpus.ps1
```

## Safety invariants

- dry-run is the default;
- persistent worker must be stopped;
- sources stay compressed and XML is streamed during ingestion;
- no filename-derived effective dates;
- no same-date revision precedence guessing;
- successful packages must form a strict manifest prefix;
- a registry package outside the manifest blocks full-corpus replay;
- failure retry remains full-package cleanup and replay;
- manifest acceptance requires the exact manifest SHA set to be registered and successful;
- Assignment remains USPTO recorded-interest evidence, never a MarkOrbit legal-title conclusion.
