# CN Lightweight Serving-State Checkpoint

## Purpose

`CN_M16_LIGHTWEIGHT_SERVING_CHECKPOINT_V1` is the minute-scale, read-only target-host evidence path used after the large CN package has already been validated and accepted operationally.

It exists so a later release gate can prove that the current CN control plane and serving layer are still coherent **without** replaying, rescanning, or semantically re-auditing the multi-billion-row CN corpus.

This checkpoint is not a replacement for a deliberately scheduled deep full-corpus audit. It must never be described as one.

## Evidence boundary

The checkpoint is allowed to read only:

- `control.source_package` in PostgreSQL, to confirm the expected package is `SUCCESS` and no CN package is `PROCESSING`;
- `system.tables` in ClickHouse, to confirm critical serving tables exist;
- `system.parts` in ClickHouse, to confirm each critical table has active parts and to read part-level byte/row metadata;
- `system.columns` in ClickHouse, to validate the exact `cn_goods_item_current` schema contract;
- `system.disks` in ClickHouse, to capture current capacity/free-space metadata.

It does not read the CN fact tables themselves.

The implementation and tests forbid corpus `FINAL`, table JOINs, `uniqExact`, corpus `GROUP BY`, `OPTIMIZE`, mutations, `system.part_log`, replay, import, package rescan, and worker/service lifecycle actions.

The persisted JSON explicitly records:

- `full_corpus_scan: false`
- `package_reprocessed: false`
- `full_corpus_semantic_acceptance_claimed: false`

The M1.7 runtime gate rejects lightweight evidence if any of these boundaries are violated.

## Status policy

`PASS` means the expected package is `SUCCESS`, the CN package control plane is quiescent, required serving tables have active parts, the exact goods schema is present, and ClickHouse reports at least 20% free space on each reported usable disk.

`WARN` is currently reserved for low ClickHouse free space: at least 10% but below 20%. It is acceptable runtime evidence, but the M1.7 gate surfaces `PASS_WITH_WARNINGS`.

`BLOCKED` is returned for missing/non-success package state, any CN package currently `PROCESSING`, missing critical tables/parts, schema drift, missing disk metadata, less than 10% free space, or checkpoint execution failure.

No status causes a mutation.

## Target-host operator flow

The repository code must first be current. PostgreSQL and ClickHouse must already be running and reachable through the normal repository configuration. The scripts do not start or restart them.

Generate the lightweight persisted evidence:

```powershell
cd D:\yoomarks\markorbit-data-engine
.\scripts\check-cn-serving-state.ps1 -ExpectedFileName 2023_5.zip
```

The script writes a report under `reports\cn_serving_state_2023_5.zip_<timestamp>.json` and prints the exact path.

Then consume that persisted report in the M1.7 gate:

```powershell
.\scripts\check-platformization-m17.ps1 `
  -CnServingCheckpointPath <REPORT_PATH> `
  -ExpectedCnFileName 2023_5.zip
```

The M1.7 gate is local-Python by default. Docker execution remains explicit opt-in through `-UseDocker`; the gate itself never runs CN database queries and only reads the persisted JSON evidence.

## Release semantics

A successful lightweight gate records promotion basis:

`PERSISTED_LIGHTWEIGHT_SERVING_STATE_AFTER_PRIOR_VALIDATION`

This means current target-host serving/control state is accepted as release evidence **after prior operator-accepted package validation**. It does not assert that the lightweight checkpoint revalidated every CN row.

The gate does not edit `VERSION`. Release promotion remains a separate explicit repository change after the evidence gate passes.

A future intentionally scheduled deep audit remains a separate concern and must not be silently substituted into this lightweight operator path.

## CI and merge contract

Changes to the checkpoint, its evidence schema, the Windows operators, or the M1.7 runtime gate must pass the repository's complete pull-request workflow set on the **exact current PR head SHA** before merge. Results from an earlier head are not reusable after any code, test, or documentation commit that changes the PR head.

At minimum, the generic Python/Ruff/Pytest job, Windows PowerShell parsing/contracts, runtime image checks, the M1.7 static checkpoint, the bounded CN real-package E2E, and all repository domain workflows must complete successfully. A lightweight target-host report is runtime evidence and cannot be used to bypass repository CI.

Likewise, CI evidence cannot replace the target-host report: repository CI proves the implementation contract, while `CN_M16_LIGHTWEIGHT_SERVING_CHECKPOINT_V1` proves the current configured target's serving/control state. Both layers remain distinct.
