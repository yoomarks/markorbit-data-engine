# CN M1.6 Post-Import Acceptance

## Purpose

Use this read-only gate immediately after a long-running CN source package finishes.
It verifies the named package in `control.source_package`, reuses the authoritative
M1.6 replay-readiness checks, and selects the next safe action without rebuilding,
recreating, starting, or restarting the persistent worker.

The first production use is the completed `2023_4.zip` monthly patch.

## Operator command

From the repository root on the machine that owns the CN PostgreSQL/ClickHouse data:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-post-import.ps1 -ExpectedFileName 2023_4.zip
```

PostgreSQL and ClickHouse must already be running. The command is read-only and writes
its JSON evidence to `reports/cn_m16_post_import_2023_4.zip_<timestamp>.json` unless an
explicit `-OutputPath` is supplied.

## Decision contract

The gate first requires an exact `CN` source-package row for the expected file and at
least one matching row in `SUCCESS` state. It then reuses `CN_M16_REPLAY_READINESS_V1`.

- `READY_TO_CONTINUE`: the completed package is accepted and additional registered or
  interrupted CN packages remain. Continue with the normal one-shot replay command
  reported in `next_action.command`.
- `RETRY_REQUIRED`: the completed package is accepted, but a failed/missing package is
  now the replay barrier. Resume only with the explicit `-ResumeFailed` command reported
  by the gate.
- `PASS` / `PASS_WITH_WARNINGS`: no CN replay work remains. The gate automatically ran
  the existing read-only `CN_M16_FINAL_CHECKPOINT_V1`; CN replay/storage/integrity is
  accepted for the next-domain decision.
- `BLOCKED`: stop. The expected package is not registered/SUCCESS or replay-readiness
  found a hard issue such as a running persistent worker, PROCESSING package, incomplete
  M1.6 schema, Storage V2 regression, pending ClickHouse mutation, or orphan stage rows.
- `FINAL_CHECKPOINT_FAILED`: replay is complete but the final M1.6 integrity checkpoint
  did not pass. Do not start the next domain until its reasons are resolved.

## Safety boundary

This gate does not mutate package status, clean stage tables, replay data, compact facts,
reset volumes, or start the persistent worker. The expensive final acceptance audit is
short-circuited until both the expected package is `SUCCESS` and replay-readiness is
`COMPLETE`.

Do not run a blind full replay after `2023_4.zip`. Follow the machine-readable
`next_action` selected from the actual PostgreSQL and ClickHouse state.
