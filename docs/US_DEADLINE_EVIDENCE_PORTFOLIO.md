# US Reviewed Event Evidence and Deadline Portfolio

Status: reviewed-evidence workflow; **no production event-role mappings ship with the repository**.

This layer sits between official USPTO event facts and the existing application/maintenance deadline calculators. It exists to automate docket evidence only after an event code has been reviewed and bound to a specific official USPTO event-reference version.

## 1. Evidence layers

1. `markorbit_facts.us_event_history` — raw USPTO event facts.
2. `reference.us_trademark_event_*` — versioned official USPTO event-code reference text with retained source SHA evidence.
3. `interpretation.us_event_role_*` — separately reviewed mapping from an official event code to a narrow procedural role used by deadline automation.
4. deadline evidence resolver — converts only reviewed roles into OA/NOA/SOU/extension input facts.
5. deadline calculators / candidate scan — calculate filing-window metadata; never application legal status.

Unknown event codes stop at layer 1/2. They are not guessed into a procedural role.

## 2. Allowed reviewed roles

The initial role vocabulary is deliberately narrow:

- `OFFICE_ACTION_NONFINAL_ISSUED`
- `OFFICE_ACTION_FINAL_ISSUED`
- `OFFICE_ACTION_RESPONSE_FILED`
- `NOTICE_OF_ALLOWANCE_ISSUED`
- `STATEMENT_OF_USE_FILED`
- `ITU_EXTENSION_GRANTED`
- `OPPOSITION_EXTENSION_30_GRANTED`
- `OPPOSITION_EXTENSION_90_GRANTED`
- `OPPOSITION_EXTENSION_150_GRANTED`

Adding a new role requires a new schema/rule review; arbitrary strings cannot enter the database.

## 3. Import safety

An event-role ruleset must:

- bind to the exact active official USPTO event-reference version;
- map only event codes present in that active official reference;
- have a retained local evidence document and matching SHA-256;
- have a deterministic normalized-payload SHA-256;
- map each event code at most once per ruleset;
- carry a rationale and source references for every mapping.

Production import:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\import-us-event-roles.ps1 `
  -RulesetFileName reviewed_event_roles_v1.json
```

The JSON and its evidence document live under:

```text
RAW_DATA_PATH/reference/us/interpretation/
```

CI uses synthetic mappings solely to prove the mechanism. Production mappings shipped by this milestone: **0**.

## 4. Conservative automatic evidence resolution

`app.us.deadline_evidence.resolve_deadline_evidence()` fails closed:

- no active reviewed ruleset -> no automatic OA/NOA inputs;
- event/reference evidence missing or hash-mismatched -> no automatic inputs;
- unknown event code -> remains unmapped;
- latest mapped OA followed by a mapped response -> OA is treated as responded for candidate generation and no pending OA input is emitted;
- multiple distinct mapped NOA dates -> ambiguity, no NOA deadline input;
- ITU extension count comes only from mapped `ITU_EXTENSION_GRANTED` observations after the resolved NOA;
- more than five mapped ITU grants -> ambiguity, not a sixth extension;
- mapped SOU is a filing fact only and does not imply timeliness or acceptance;
- opposition extension totals come only from reviewed grant-role events.

Explicit operator/API evidence always overrides automatic reviewed-event evidence.

## 5. Read-only API

The deadline docket router is included beneath the existing `/api/us` semantic router and exposes GET-only endpoints:

- `GET /api/us/event-roles/ruleset`
- `GET /api/us/deadline-evidence/{serial_number}`
- `GET /api/us/application-deadlines-resolved/{serial_number}`
- `GET /api/us/deadlines/candidates`

The resolved application-deadline response reports provenance for every input:

- `EXPLICIT_API_EVIDENCE`
- `OFFICIAL_USPTO_CASE_FACT`
- `REVIEWED_EVENT_ROLE_EVIDENCE`
- `MISSING`

## 6. Deadline candidate portfolio

The candidate scanner combines only auditable inputs:

- maintenance windows from official registration dates + versioned maintenance rules;
- publication opposition from official `publication_date`;
- OA/NOA/extension candidates only when reviewed event-role evidence passes.

Every candidate contains:

- serial / registration number;
- family and code;
- nominal due date;
- bounded urgency bucket;
- evidence source;
- rule/evidence details;
- `legal_status_inference=false`.

A candidate is **not** a conclusion that a filing is still legally available or that a case is active. Recent past nominal deadlines are retained only as investigation candidates.

## 7. Lossless bounded scan

API pagination is by serial-number case page, not by result offset. The API caps `scan_limit` at 500 and reserves a 5,000-candidate buffer. If that buffer is ever exceeded, the request fails with an instruction to lower `scan_limit`; it never silently truncates and advances the cursor.

For full-corpus offline export:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\export-us-deadline-candidates.ps1 `
  -OutputFileName us_deadline_candidates.jsonl `
  -AsOf 2026-08-09 `
  -HorizonDays 90 `
  -RecentPastDays 30 `
  -BatchSize 500
```

The JSONL exporter uses the same serial cursor and aborts if a page could be lossy.

## 8. Next production prerequisite

Before OA/NOA auto-evidence is enabled on real USPTO data, the real official event-code reference must be source-backed and accepted, followed by a human-reviewed event-role mapping ruleset. Until then, direct case facts (maintenance/publication) continue to work while event-derived automation remains disabled.
