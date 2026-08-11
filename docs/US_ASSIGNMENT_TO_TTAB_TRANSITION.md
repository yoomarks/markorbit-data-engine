# US Assignment → TTAB Transition Gate

`US_ASSIGNMENT_TO_TTAB_TRANSITION_V1` encodes the third domain boundary in the frozen Data Engine corpus order:

1. CN
2. US Application
3. US Assignment
4. US TTAB
5. final four-domain acceptance

## Chained prerequisite

The gate calls the existing US Application → Assignment transition gate and requires `ASSIGNMENT_ACCEPTED` with `assignment_ready=true` before TTAB readiness is evaluated.

Because the Assignment transition is itself chained, this inherits all upstream prerequisites:

- CN final checkpoint accepted;
- US Application source-backed accepted;
- US Assignment source-backed accepted.

A merely unlocked Assignment phase is not enough to evaluate TTAB.

## TTAB behavior

After Assignment acceptance, the gate delegates to `US_TTAB_READINESS_V1` and preserves that contract unchanged.

- `SOURCE_NOT_REGISTERED`, `NOT_READY`, source verification requirements, and other non-terminal states return `TTAB_PHASE_UNLOCKED` with the existing TTAB next action.
- TTAB readiness states with `ready=true` return `TTAB_ACCEPTED`.
- Non-fatal source/data coverage warnings remain visible.
- No deadline-validity, substantive-rights, or legal-outcome conclusion is inferred.

Unlocking the TTAB phase does not register a snapshot, ingest a package, retry a package, or run any replay.

## Operator command

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\check-us-ttab-transition.ps1 `
  -ExpectedHistoryParts 91
```

Optional verification flags:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\check-us-ttab-transition.ps1 `
  -ExpectedHistoryParts 91 `
  -DeepSourceTest `
  -VerifyUSSourceFiles `
  -VerifyAssignmentSources `
  -VerifyTTABSources
```

A timestamped JSON report is written under `reports/` unless `-OutputPath` is supplied.

## Safety boundary

The transition gate is read-only. It does not register/stage sources, apply schemas, acquire ingestion locks, ingest/retry packages, reset tables, run replay, infer legal outcomes, or mutate PostgreSQL/ClickHouse.
