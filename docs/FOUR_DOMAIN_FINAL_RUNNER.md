# One-command Four-domain Final Acceptance Runner

`run-four-domain-final-acceptance.ps1` is an operator wrapper around the already-frozen domain audits and `MARKORBIT_FOUR_DOMAIN_ACCEPTANCE_V1`.

It does not introduce a new acceptance model. Its purpose is to prevent manual report mix-ups and preserve one auditable run directory.

## Preconditions

The runner first executes the unified domain lifecycle status and requires:

```text
current_phase = FINAL_ACCEPTANCE
status = FINAL_ACCEPTANCE_REQUIRED
```

If CN, US Application, Assignment, or TTAB is incomplete or blocked, the runner stops before generating the four heavyweight formal reports.

## Formal report sequence

Once the lifecycle precondition passes, the runner executes the existing reports in frozen order:

1. CN — `audit-m16-acceptance.ps1`
2. US Application — `audit-us-real-data.ps1`
3. US Assignment manifest — `audit-us-assignment-corpus.ps1`
4. US TTAB manifest — `audit-us-ttab-corpus.ps1`
5. Existing final gate — `audit-four-domain-acceptance.ps1`

The Assignment and TTAB **manifest acceptance** reports are deliberately used because those are the formal report identities expected by `MARKORBIT_FOUR_DOMAIN_ACCEPTANCE_V1`. Their separate real-data/readiness reports are not substituted.

## Required pinned Application policy

Both values are explicit operator inputs:

- `ExpectedApplicationHistoryParts`
- `ExpectedApplicationDailyThrough` (`YYYY-MM-DD`)

The final gate receives the same pinned history-part count used to generate the Application acceptance report.

Example:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\run-four-domain-final-acceptance.ps1 `
  -ExpectedApplicationHistoryParts 91 `
  -ExpectedApplicationDailyThrough 2026-08-01
```

Optional Application archive verification:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\run-four-domain-final-acceptance.ps1 `
  -ExpectedApplicationHistoryParts 91 `
  -ExpectedApplicationDailyThrough 2026-08-01 `
  -VerifyApplicationSourceFiles
```

The date above is only a syntax example; production runs must pin the authoritative expected daily coverage end for that corpus run.

## Retained run directory

The default output directory is:

```text
reports/four_domain_final_<timestamp>/
```

It contains:

```text
00_domain_lifecycle.json
01_cn_acceptance.json
02_us_application_acceptance.json
03_us_assignment_manifest_acceptance.json
04_us_ttab_manifest_acceptance.json
05_four_domain_acceptance.json
run_manifest.json
```

`run_manifest.json` records the pinned policy, current local Git HEAD when available, final status, and SHA-256 hashes for every report file. The existing final acceptance report also continues to enforce its own report-file and archive evidence checks.

## Safety boundary

The runner is read-only with respect to Data Engine facts and control state. It does not stage/register sources, apply schemas, acquire ingestion locks, retry packages, reset tables, start workers, or run any CN/US replay. It invokes only existing read-only status/audit commands.
