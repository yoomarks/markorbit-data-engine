# US Application → Assignment Transition Gate

`US_APPLICATION_TO_ASSIGNMENT_TRANSITION_V1` encodes the second domain boundary in the frozen Data Engine corpus order:

1. CN
2. US Application
3. US Assignment
4. US TTAB
5. final four-domain acceptance

## Chained prerequisite

This gate does not inspect Assignment first. It calls the existing CN → US Application transition gate and requires the result `US_APPLICATION_ALREADY_ACCEPTED`.

That means the prerequisite chain is:

- CN M1.6 replay complete;
- CN final checkpoint accepted;
- US Application source/schema/replay pipeline complete;
- US Application source-backed acceptance status `ACCEPTED`.

A US Application state of merely `REPLAY_READY` is not enough to unlock Assignment.

## Assignment behavior

After US Application acceptance, the gate delegates to `US_ASSIGNMENT_READINESS_V1` without changing its semantics.

- `SOURCE_NOT_REGISTERED`, `NOT_READY`, source verification requirements, or other non-terminal states return `ASSIGNMENT_PHASE_UNLOCKED` with the existing Assignment next action.
- Assignment readiness states with `ready=true` return `ASSIGNMENT_ACCEPTED`.
- Non-fatal recorded-interest data warnings remain visible and do not become legal-ownership conclusions.

The gate distinguishes **phase unlocked** from **Assignment accepted**. Unlocking the phase does not automatically register sources, ingest packages, retry packages, or run reconciliation.

## Operator command

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\check-us-assignment-transition.ps1 `
  -ExpectedHistoryParts 91
```

Optional verification flags:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\check-us-assignment-transition.ps1 `
  -ExpectedHistoryParts 91 `
  -DeepSourceTest `
  -VerifyUSSourceFiles `
  -VerifyAssignmentSources
```

A timestamped JSON report is written under `reports/` unless `-OutputPath` is supplied.

## Safety boundary

The transition gate is read-only. It does not stage or register US Application/Assignment sources, apply schemas, acquire ingestion locks, ingest Assignment packages, retry packages, infer title/legal ownership, reset tables, or start any replay.
