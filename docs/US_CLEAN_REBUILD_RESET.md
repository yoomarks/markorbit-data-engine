# US Clean Rebuild Reset

Status: EXPLICIT DESTRUCTIVE US-ONLY OPERATION

This operation exists only to prepare a known US source corpus for a clean deterministic replay. It is intentionally separate from the replay executor. Normal replay never truncates data or silently converts prior successes into replay candidates.

## Required sequence

1. Run source preflight and pin the authoritative historical part count.
2. Stage any authoritative archive-only source back into `raw_data/incoming/us`.
3. Run this reset in dry-run mode and inspect the report.
4. Apply reset only with the exact destructive confirmation token.
5. Run deterministic replay in dry-run mode.
6. Apply deterministic replay, stopping on the first failure.
7. Run the source-backed real-data acceptance audit with source SHA verification.

## Dry run

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\reset-us-clean-rebuild.ps1 -ExpectedHistoryParts <N>
```

Dry run is non-destructive. It requires PostgreSQL and ClickHouse to be running and the persistent worker to be stopped.

The plan is blocked unless:

- source preflight is safe;
- the latest historical coverage range is exactly the pinned `01..N` set;
- no replay source still exists only in archive;
- every registered US package belongs to the current authoritative source plan;
- registered package kind/partition identity agrees with the source SHA;
- no duplicate registered SHA identity exists.

## Apply

The exact confirmation token is:

```text
RESET-US-M1.3
```

Apply requires both the explicit switch and the exact token:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\reset-us-clean-rebuild.ps1 -ExpectedHistoryParts <N> -DeepSourceTest -Apply -ConfirmReset RESET-US-M1.3
```

The Python module independently enforces the same exact token through `--confirm RESET-US-M1.3`; bypassing the PowerShell wrapper does not bypass destructive confirmation.

## Pre-reset evidence manifest

Before the first destructive ClickHouse command, the reset writes an evidence manifest under:

```text
raw_data/rebuild_manifests/us/
```

The manifest records:

- reset version and timestamp;
- deterministic manifest fingerprint;
- source-preflight replay plan;
- registered US package inventory relevant to reset;
- all eleven US fact-table physical row counts;
- existing package IDs, package sequences, source ranks, statuses, and source SHA identities;
- the exact registry rows that will be returned to `REGISTERED`.

The report also returns the SHA-256 of the manifest file. The manifest is evidence of the pre-reset state; it is not a database backup. Authoritative USPTO ZIP/XML files remain the rebuild source.

## What reset changes

The operation acquires the existing US ingestion advisory lock, then:

1. re-runs the reset plan under the lock;
2. persists the pre-reset evidence manifest;
3. truncates only the eleven `markorbit_facts.us_*` fact tables;
4. preserves existing `control.source_package.package_id` and `package_sequence` identities;
5. resets registered source-plan US packages to `REGISTERED`;
6. clears their package profiles, archived path, processed timestamp, and error message;
7. recomputes modeled file path, package metadata, source sequence, and source rank using the preserved package sequence;
8. verifies every US fact table is empty;
9. verifies remaining US registry rows are `REGISTERED`.

Unregistered authoritative source-plan packages remain unregistered. The deterministic replay executor will register them when their turn arrives.

## What reset does not change

It does not:

- delete US package identities;
- delete or truncate CN fact tables;
- mutate non-US source-package registry rows;
- stage files from archive;
- start the persistent worker;
- run replay automatically;
- infer trademark legal status, attorney role, or maintenance conclusions.

## Failure ordering

ClickHouse fact tables are truncated before PostgreSQL package statuses are reset. This ordering is deliberate. If the later PostgreSQL reset fails, old `SUCCESS` statuses remain while US fact tables are empty. That state is fail-closed: the deterministic replay executor will not silently proceed as though the old successes were valid. Re-run the guarded reset after investigating the failure.

## Completion is not acceptance

`RESET_COMPLETE` means the US fact layer is empty and existing source-plan package identities are ready for deterministic replay. It does not validate the rebuilt corpus.

After replay reaches `COMPLETE`, run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-us-real-data.ps1 -ExpectedHistoryParts <N> -VerifySourceFiles
```

Only that final acceptance audit establishes database/source integrity for the locally replayed corpus.
