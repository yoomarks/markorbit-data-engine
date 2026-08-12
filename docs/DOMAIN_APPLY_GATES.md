# Domain Apply Gates

## Purpose

Data Engine corpus mutation must follow the frozen domain order:

1. CN
2. US Application
3. US Assignment
4. US TTAB
5. final four-domain acceptance

Read-only transition diagnostics are not sufficient if a legacy or deterministic mutation script can bypass them. The apply gate therefore runs immediately before every supported US ingestion/retry entrypoint.

## Policy

- US Application mutation requires the CN final checkpoint to pass.
- US Assignment mutation requires US Application to be source-backed accepted through the chained CN -> Application transition gate.
- US TTAB mutation requires US Assignment to be accepted through the chained CN -> Application -> Assignment -> TTAB transition gate.
- The gate is evaluated once per operator command, before schema apply or ingestion mutation.
- `-All` replay does not re-run the gate for every package.
- No static permit file is trusted across commands; each mutation command re-evaluates current durable state.
- The gate itself is read-only apart from writing its JSON audit report under `reports/`.

## Shared entrypoint

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\assert-domain-apply-gate.ps1 `
  -TargetDomain US_APPLICATION `
  -ExpectedApplicationHistoryParts 91
```

Operators normally do not call this directly. Mutation wrappers call it automatically.

## Deterministic replay examples

US Application:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\replay-us-deterministic.ps1 `
  -ExpectedHistoryParts 91 `
  -Apply -All
```

US Assignment:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\replay-us-assignment-deterministic.ps1 `
  -ExpectedApplicationHistoryParts 91 `
  -Apply -All
```

US TTAB:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\replay-us-ttab-deterministic.ps1 `
  -ExpectedApplicationHistoryParts 91 `
  -Apply -All
```

Legacy one-shot and retry wrappers require the same pinned history-part count and use the same shared apply gate.

## Failure behavior

If an upstream domain is incomplete, not source-backed accepted, or its final checkpoint fails, the downstream mutation command terminates before target schema application or package ingestion starts. Do not bypass the gate with direct database writes or ad-hoc Python invocation.
