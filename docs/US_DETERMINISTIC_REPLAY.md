# US Deterministic Replay Executor

Status: EXPLICIT STOP-ON-FAILURE REPLAY

The deterministic replay executor consumes the source order produced by the US source preflight. It is the controlled mutation step after source preflight and, when needed, archive staging.

It does not discover a different order from the database queue. The source preflight plan is the only allowed ordering authority: historical coverage parts first in deterministic part order, followed by daily packages in ascending update-date order.

## Dry run

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\replay-us-deterministic.ps1 -ExpectedHistoryParts <N>
```

Dry run is the default. PostgreSQL and ClickHouse must already be running, and the persistent worker must be stopped.

The dry run reads:

- the current source preflight plan;
- the US package registry;
- existing package status, source rank, schema/profile version, and source SHA lineage.

It does not register or ingest a package.

## Apply one package

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\replay-us-deterministic.ps1 -ExpectedHistoryParts <N> -DeepSourceTest -Apply
```

By default, apply processes at most one package. This keeps the same operational safety as the existing one-shot runner while making the ordering source explicit.

## Apply the full remaining plan

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\replay-us-deterministic.ps1 -ExpectedHistoryParts <N> -DeepSourceTest -Apply -All
```

`-All` processes the remaining ordered plan and stops immediately on the first package failure. Later packages are never attempted after that failure.

A bounded batch can be requested instead:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\replay-us-deterministic.ps1 -ExpectedHistoryParts <N> -Apply -MaxPackages 3
```

## Ordering and continuation rules

A valid existing registry state must be a strict successful prefix of the source plan, followed by unfinished packages. The executor rejects a state in which a later package is already `SUCCESS` while an earlier package is unfinished.

For each step:

- no registry row -> `REGISTER_AND_INGEST`;
- `REGISTERED` -> `INGEST`;
- `FAILED`, `MISSING_FILE`, or `INTERRUPTED` -> `RETRY_FULL_PACKAGE`;
- `PROCESSING` -> recovered to `INTERRUPTED` under the US advisory lock before execution;
- `SUCCESS` -> skipped only when the successful package profile is `US_M1.3` and its profile source SHA matches the authoritative source SHA.

Retry is always a full-package replay. Existing package-scoped output cleanup remains inside `ingest_us_package`; there is no XML-member checkpoint.

## Blocking conditions

Execution is blocked when:

- source preflight is not safe;
- an unfinished source exists only in archive and therefore still needs explicit staging;
- the US registry contains a package not present in the current authoritative source plan;
- registry package kind/partition identity does not match its source SHA;
- a successful package has an old/non-M1.3 profile or mismatched profile SHA;
- a later package is successful before an earlier unfinished package;
- registered source ranks do not increase in source-plan order;
- an unknown registry status is encountered.

## Concurrency

Apply acquires the existing independent US advisory lock `markorbit:us:package-ingestion`. If another US ingestion holds the lock, the executor returns `BUSY`. Persistent worker execution is additionally blocked by the PowerShell wrapper so a manual deterministic replay cannot race the normal worker loop.

## File behavior

The executor does not auto-stage archived sources. Pending archive-only sources must first pass `stage-us-replay-sources.ps1`.

Successful ingestion keeps the existing package behavior: the authoritative incoming package is moved to `raw_data/archive/us`. A later executor invocation can still recognize that archived source as the already-successful prefix while requiring any unfinished source to remain in incoming.

## Completion is not acceptance

`COMPLETE` means every source-plan package is registered and successfully replayed in order. It does **not** mean the real corpus has passed final acceptance.

After completion, run the source-backed acceptance audit:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-us-real-data.ps1 -ExpectedHistoryParts <N> -VerifySourceFiles
```

Only that final audit verifies all eleven durable tables, `FINAL` uniqueness, orphan/lineage/rank integrity, replay profile versions, history/daily coverage, and authoritative source-file SHA evidence.

## Destructive reset boundary

This executor intentionally does not reset or truncate existing US data. A future clean-rebuild/reset command must be a separate explicit destructive operation with its own guardrails. The replay executor will not silently convert an older successful package into a replay candidate or delete unrelated US registry state.
