# US Source Preflight and Replay Plan

Status: READ-ONLY SOURCE-CORPUS GATE

This preflight runs before US package registration or replay. It inventories local USPTO application source files under both `raw_data/incoming/us` and `raw_data/archive/us`, validates that the source set is structurally safe, and emits a deterministic replay plan. It does not require PostgreSQL or ClickHouse and does not change any file or database state.

## Command

Use the authoritative historical part count for the latest historical coverage range:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\preflight-us-source-replay.ps1 -ExpectedHistoryParts <N>
```

For a deeper source integrity pass that runs ZIP CRC checks and stream-parses every XML member to EOF:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\preflight-us-source-replay.ps1 -ExpectedHistoryParts <N> -DeepSourceTest
```

The wrapper refuses to run while the persistent worker is active, because the source inventory should not change while it is being inspected. It writes a timestamped JSON report under `reports/` unless `-OutputPath` is supplied.

## What it checks

The preflight:

- recognizes only modeled USPTO application package names;
- rejects `.gz` as unsupported by the US M1 application-package contract;
- computes SHA-256 for every recognized physical source;
- reads ZIP central directories and requires at least one readable XML member;
- rejects encrypted or duplicate ZIP members;
- optionally CRC-checks ZIPs and stream-parses every XML member to EOF;
- deduplicates identical copies of the same semantic source across incoming/archive;
- rejects the same semantic partition when different SHA-256 values are present;
- applies the strict historical `01..N` completeness policy;
- never guesses the historical trailing part count;
- rejects a daily package dated on or before the latest historical baseline end;
- reports calendar gaps between observed daily dates as informational only;
- emits history first, then daily packages, in deterministic source order;
- marks replay-plan rows that currently live only in archive and therefore require a later explicit staging step.

## Why old daily packages are blocked

The US M1 precedence model deliberately ranks daily application updates above historical snapshots. That is correct only for daily updates that occur **after** the chosen historical baseline ends. Replaying a daily file dated on or before the historical snapshot end would give older daily content higher precedence than a newer historical snapshot. The preflight therefore treats this as a hard source-plan failure.

## Daily calendar gaps

A gap between observed daily dates is **not** automatically a missing file. Weekends, federal holidays, and USPTO publication schedules may produce legitimate calendar gaps. The preflight records these gaps but does not infer an expected daily publication calendar. A future authoritative manifest can turn specific missing expected dates into enforceable evidence without guessing.

## Status

- `PASS`: source set is structurally safe and historical completeness is pinned and satisfied.
- `PASS_WITH_WARNINGS`: safe to replay, but non-blocking source conditions exist, such as no daily package yet or identical copies that were deduplicated in the plan.
- `NOT_READY`: no complete/pinned historical baseline is available.
- `FAIL`: source container/XML is unreadable, package identity is unknown/unsupported, a semantic partition has conflicting SHA values, or a daily source is unsafe relative to the historical baseline.

`safe_to_replay` is true only for `PASS` and `PASS_WITH_WARNINGS`.

## Boundary

This tool plans only. It does not stage archived files into incoming, register packages, run ingestion, retry failures, apply schema, infer trademark legal status, or decide whether a calendar date should have had a USPTO daily publication.
