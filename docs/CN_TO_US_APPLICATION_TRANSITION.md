# CN → US Application Transition Gate

`CN_TO_US_APPLICATION_TRANSITION_V1` encodes the corpus order boundary between China M1.6 and US Application replay.

Frozen order:

1. CN
2. US Application
3. US Assignment
4. US TTAB
5. final four-domain acceptance

## Gate behavior

The gate is read-only and always evaluates the CN final checkpoint first.

- If CN is not `PASS` or `PASS_WITH_WARNINGS`, the result is `BLOCKED_BY_CN` and the US pipeline builder is not called.
- If CN is accepted, the gate delegates US source/schema/replay/acceptance inspection to the existing `US_PIPELINE_READINESS_V1` implementation.
- `safe_to_start_us_replay=true` only when the US pipeline state is exactly `REPLAY_READY`.
- If US Application is already `ACCEPTED`, the gate reports `US_APPLICATION_ALREADY_ACCEPTED` and does not recommend replay.
- Source, schema, staging, reset, replay, or acceptance blockers are reported as `US_APPLICATION_NOT_READY` with the existing US pipeline reason codes and next action.

The transition gate itself never stages sources, applies schema, registers packages, resets tables, runs replay, or starts a worker.

## Operator command

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\check-us-application-transition.ps1 `
  -ExpectedHistoryParts 91
```

`-ExpectedHistoryParts` remains explicit rather than hard-coded. The operator must pin the authoritative expected historical tail count used by the existing US source-preflight contract.

Optional source checks:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\check-us-application-transition.ps1 `
  -ExpectedHistoryParts 91 `
  -DeepSourceTest `
  -VerifySourceFiles
```

A timestamped JSON report is written under `reports/` unless `-OutputPath` is supplied.

## Why the replay script is not automatically wrapped yet

The CN final checkpoint includes the full CN integrity acceptance scan once CN replay is complete. Running that expensive scan before every individual US `-Apply` package would be wasteful. This transition gate is therefore the canonical read-only cross-domain decision point. A future hard apply-time guard should use a validated transition checkpoint/lease mechanism rather than repeatedly rescanning the full CN corpus.

## Safety boundary

The gate performs reads and source-file inspection only. It does not acquire ingestion locks, mutate PostgreSQL/ClickHouse, run `OPTIMIZE`, stage archives, reset US state, or start US replay.
