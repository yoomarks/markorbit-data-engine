# US Assignment — Real-Data Acceptance and Cross-Layer Evidence Reconciliation

This layer validates the `US_ASSIGNMENT_M1.0` recorded-interest corpus and compares it with US M1.4 case-owner observations without deciding legal title.

## Acceptance

Read-only acceptance checks:

- Assignment schema version and four durable tables
- registered package status / M1.0 replay profile
- append-only observation-key uniqueness
- assignor/assignee/property rows have a matching reel/frame + source package record
- every durable row resolves to a registered package and the registered `source_rank`
- latest reel/frame projection is unique
- malformed property serials are reported as data-quality warnings, not silently repaired
- optional authoritative local source SHA-256 verification
- coverage of current latest Assignment property serials against US case-file serials

Without source-file verification a structurally clean corpus returns `PASS_WITH_WARNINGS` with `assignment_source_sha_verification_not_requested`. With successful verification it returns `PASS`.

## Readiness states

`GET /api/us/assignments/readiness`

Possible states include:

- `SOURCE_NOT_REGISTERED`
- `NOT_READY`
- `SOURCE_VERIFICATION_REQUIRED`
- `ACCEPTED_WITH_DATA_WARNINGS`
- `ACCEPTED`
- `FAILED`

Readiness is a data-pipeline state only.

## Forward reconciliation

`GET /api/us/assignments/reconciliation`

For each serial referenced by the latest observation of a reel/frame, the engine compares:

- current case-file owner names
- latest recorded-assignment assignee names
- latest durable case-owner observation metadata

Classifications:

- `NAME_SET_MATCH`
- `NAME_SET_DIFFER`
- `RECORDED_ASSIGNMENT_WITHOUT_CASE_RECORD`
- `RECORDED_ASSIGNEE_NAMES_MISSING`
- `CASE_OWNER_NAMES_MISSING`
- `NOT_COMPARABLE`

Comparison is only whitespace/case-normalized exact name-set equality. It does not perform fuzzy entity resolution.

## Reverse owner-change evidence gaps

`GET /api/us/assignments/owner-change-gaps`

The reverse scan starts from US M1.4 cases where durable `owner_set_hash` has more than one observed value. It reports whether a current latest Assignment property references that serial:

- `CASE_OWNER_CHANGE_WITH_RECORDED_ASSIGNMENT_EVIDENCE`
- `CASE_OWNER_CHANGE_WITHOUT_RECORDED_ASSIGNMENT_EVIDENCE`

The second label is only an evidence gap. It does **not** prove that no valid assignment, merger, name change, corporate succession, or other ownership event occurred.

## Commands

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-us-assignment-real-data.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-us-assignment-real-data.ps1 -VerifySourceFiles
powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-us-assignment-readiness.ps1 -VerifySourceFiles
powershell.exe -ExecutionPolicy Bypass -File .\scripts\export-us-assignment-reconciliation.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\export-us-assignment-owner-change-gaps.ps1
```

All APIs and commands are read-only.

## Legal boundary

Every reconciliation/report layer carries `legal_ownership_conclusion=false`.

A recorded USPTO Assignment record, a case-file owner observation, a match between names, a mismatch between names, or the absence of a matching recorded Assignment are all evidence/data observations. None alone establishes legal title or transaction validity.
