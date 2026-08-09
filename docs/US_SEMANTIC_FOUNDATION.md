# US Semantic Foundation

Status: EVIDENCE-FIRST FOUNDATION — NO PRODUCTION LEGAL RULES SHIPPED

This layer sits above the frozen US M1.3 official fact model. It does not mutate USPTO facts and does not convert a raw status/event code into a legal conclusion unless a separately imported, evidence-bound MarkOrbit ruleset explicitly does so.

## Layers

1. **Official facts (ClickHouse)** — raw USPTO status codes, status dates, events, filing bases, owners, classes, statements, Madrid facts, and source lineage.
2. **Official reference (PostgreSQL `reference`)** — versioned USPTO-published descriptions for status and event codes.
3. **Reference evidence gate** — verifies the locally retained source document against the SHA-256 recorded by the active reference version and checks that every currently observed code is mapped.
4. **Semantic readiness** — requires both source-backed US corpus acceptance and source-backed status/event reference acceptance. Passing this gate means only `READY_FOR_RULE_RESEARCH`.
5. **Interpretation rules (`interpretation`)** — optional MarkOrbit-derived rules, versioned separately and bound to exact active status/event reference versions. The repository ships no production rules in this milestone.

## Fail-closed interpretation contract

`app.us.status_interpretation.interpret_status()` returns `UNKNOWN` when any of the following is true:

- no active ruleset exists;
- the ruleset is bound to a different official reference version;
- the ruleset evidence file is missing or its SHA-256 changed;
- either official reference evidence file is missing or changed;
- no rule matches;
- multiple top-priority rules produce different outputs.

Only one uniquely matched top-priority rule with verified evidence may return a non-`UNKNOWN` derived result.

## Evidence layout

```text
RAW_DATA_PATH/reference/us/status/
RAW_DATA_PATH/reference/us/event/
RAW_DATA_PATH/reference/us/interpretation/
```

A legacy status source directly under `RAW_DATA_PATH/reference/us/` remains readable for backward compatibility.

## Operations

Apply all additive US schemas:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\apply-us-m1-schema.ps1
```

Import event reference:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\import-us-event-reference.ps1 -ReferenceFileName <file.json>
```

Audit both official references:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-us-official-references.ps1
```

Check semantic readiness:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\us-semantic-readiness.ps1 -ExpectedHistoryParts <N> -DeepSourceTest
```

Import a reviewed evidence-bound ruleset only after rule research:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\import-us-status-ruleset.ps1 -RulesetFileName <file.json>
```

## Current production boundary

No production status rule mapping is included. CI uses deliberately synthetic status/event descriptions and a synthetic interpretation rule solely to prove versioning, evidence verification, conflict handling, and fail-closed behavior.
