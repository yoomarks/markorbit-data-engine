# US M1.3 Real-Data Acceptance

Status: READ-ONLY POST-REPLAY ACCEPTANCE AUDIT

This audit is the gate between successful US M1.3 implementation tests and a locally replayed real USPTO corpus. It does not reset databases, register packages, ingest XML, retry failed packages, or change any official fact row.

Run it only after the intended historical coverage parts and subsequent daily packages have been replayed with the persistent worker stopped.

## Historical part completeness

Historical application snapshots are modeled as coverage-part files such as:

```text
apc18840407-20251231-01.zip
apc18840407-20251231-02.zip
...
```

Strict acceptance requires the latest historical coverage range to:

- start at part `01`;
- contain every part continuously through the observed maximum;
- have an explicitly pinned total part count;
- contain exactly `01..N` when `N` is supplied via `-ExpectedHistoryParts`;
- contain no part `00`, no interior gap, no missing tail part, and no part beyond the pinned total.

The filename suffix only identifies a part; it does not prove how many trailing parts exist. Therefore the audit does **not** guess the total part count. Without `-ExpectedHistoryParts`, the result is `NOT_READY` even when all observed parts are continuous.

## Command

Database integrity audit with a pinned historical tail count:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-us-real-data.ps1 -ExpectedHistoryParts <N>
```

Full source-backed acceptance including authoritative ZIP SHA-256 verification:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-us-real-data.ps1 -ExpectedHistoryParts <N> -VerifySourceFiles
```

The script writes a timestamped JSON report under `reports/` unless `-OutputPath` is supplied.

The wrapper refuses to run while the persistent `worker` service is running. PostgreSQL and ClickHouse must already be running. The audit does not apply schema migrations automatically; schema readiness is part of the result.

## Status meanings

### `PASS`

The registered real corpus has:

- a successful historical baseline with exactly the pinned `01..N` part sequence;
- at least one successful daily package;
- no pending, failed, or missing registered US package;
- successful package profiles produced under `US_M1.3`;
- US M1.3 registered in PostgreSQL and ClickHouse;
- historical source ranks below daily source ranks;
- no ambiguous same-partition different-SHA sources;
- no duplicate durable identities after ClickHouse `FINAL`;
- no subordinate serial number without a current US case;
- no current/history row whose package UUID is absent from the US package registry;
- no source-rank mismatch between durable rows and their registered package;
- populated M1.3 official fact tables after a historical + daily replay;
- at least one current case whose latest source is a daily package;
- and every successful authoritative source file exists and matches its registered SHA-256.

### `PASS_WITH_WARNINGS`

Database and historical-part integrity pass, including the pinned total part count, but `-VerifySourceFiles` was not requested. This is suitable for a fast database-only validation; full source-backed acceptance should include SHA verification.

### `NOT_READY`

The replay/evidence is incomplete rather than corrupt. Examples:

- no successful historical baseline yet;
- no successful daily update yet;
- packages remain `REGISTERED`, `PROCESSING`, or `INTERRUPTED`;
- a previously successful package profile was produced under an older US schema version and therefore requires M1.3 replay;
- the runtime schema version has not reached M1.3;
- the historical total part count has not been pinned;
- historical part `01` is missing;
- a historical part sequence has an interior gap;
- the pinned total says a tail part is missing or an observed part exceeds the pinned total;
- a historical partition identity cannot be recognized safely.

### `FAIL`

A durable integrity or source-evidence invariant is broken. Examples:

- `FAILED` or `MISSING_FILE` registered package;
- historical/daily source-rank precedence violation;
- same semantic partition represented by different registered SHA values;
- duplicate identity after `FINAL`;
- orphan subordinate facts;
- unregistered package lineage or package/source-rank mismatch;
- an M1.3 fact table remains empty after a historical + daily replay;
- daily packages are successful but no current case is sourced from daily data;
- authoritative source file missing or SHA mismatch when source verification is requested.

## Report sections

`packages` inventories replay completion, status counts, old-profile replay requirements, and ambiguous partitions.

`coverage` reports historical start/end, daily start/end, history/daily rank boundary, and how many current cases are presently sourced from historical versus daily observations.

`historical_part_completeness` reports the latest historical coverage range, observed part suffixes, missing parts through the observed maximum, the pinned expected suffixes, missing expected parts, unexpected parts, and whether strict completeness is satisfied.

`tables` reports active/current row count, unique durable identity count, distinct serial count, and duplicate identities after `FINAL` for all eleven M1.3 tables.

`integrity` reports duplicate tables, subordinate-orphan counts, source package lineage anomalies, source-rank mismatches, and empty tables.

`snapshot_reconciliation` aggregates `snapshot_tombstone_counts` already recorded in successful package profiles. It reports totals, per-table totals, per-package tombstone counts, and tombstone-to-published-row rates. These are operational reconciliation metrics, not legal-status statistics.

`source_file_verification` records whether raw source verification ran and, when requested, every missing or mismatched authoritative package.

## Important boundaries

A high tombstone rate is not itself a failure. Tombstones mean a newer complete USPTO case snapshot omitted a previously current replaceable child identity. The audit records this rate so real history→daily behavior can be reviewed empirically.

Likewise, the audit does not infer trademark legal status, attorney role, Section 8 compliance, renewal eligibility, or other legal conclusions. It validates the official fact engine and its source lineage only.
