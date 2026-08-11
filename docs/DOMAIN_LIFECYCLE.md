# MarkOrbit Data Engine Domain Lifecycle

`MARKORBIT_DOMAIN_LIFECYCLE_V1` provides one read-only status view over the frozen corpus order:

1. CN
2. US Application
3. US Assignment
4. US TTAB
5. Final four-domain acceptance

It reuses the existing chained transition gates rather than creating another set of domain acceptance rules.

## What it reports

The lifecycle report contains:

- `current_phase` — the first unfinished domain or `FINAL_ACCEPTANCE`;
- lifecycle `status`;
- the frozen domain order;
- accepted/not-accepted status for CN, US Application, US Assignment, and US TTAB;
- the existing domain `next_action` from the first unfinished gate;
- the full nested transition report for detailed diagnosis.

When all four domain gates are accepted, the lifecycle reports:

```text
current_phase = FINAL_ACCEPTANCE
status = FINAL_ACCEPTANCE_REQUIRED
next_action.code = RUN_FOUR_DOMAIN_ACCEPTANCE
```

The lifecycle does **not** replace `MARKORBIT_FOUR_DOMAIN_ACCEPTANCE_V1`. Formal final acceptance still uses the existing `audit-four-domain-acceptance.ps1` contract with four formal acceptance report files, pinned Application historical-part count, and pinned Application daily coverage end.

## Operator command

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\status-domain-lifecycle.ps1 `
  -ExpectedHistoryParts 91
```

Optional source verification:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\status-domain-lifecycle.ps1 `
  -ExpectedHistoryParts 91 `
  -DeepSourceTest `
  -VerifyUSSourceFiles `
  -VerifyAssignmentSources `
  -VerifyTTABSources
```

A timestamped JSON report is stored under `reports/` unless `-OutputPath` is supplied.

## Status semantics

- `IN_PROGRESS` — the current domain has legitimate remaining work.
- `BLOCKED` — the current domain has a blocking condition that requires investigation or repair.
- `FINAL_ACCEPTANCE_REQUIRED` — CN, US Application, Assignment, and TTAB are all accepted; run the existing formal four-domain acceptance gate.

The lifecycle command intentionally exits successfully for normal `IN_PROGRESS` or `BLOCKED` lifecycle states. It is a status command, not an apply command. Execution errors, missing database services, invalid JSON, or a running persistent worker are treated as command failures.

## Safety boundary

This lifecycle is read-only. It does not start replay, stage or register sources, apply schemas, acquire ingestion locks, retry packages, reset data, run `OPTIMIZE`, infer legal conclusions, or mutate PostgreSQL/ClickHouse.
