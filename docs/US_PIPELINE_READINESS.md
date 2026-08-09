# US Pipeline Readiness

Status: READ-ONLY NEXT-ACTION ROUTER

`app.us.pipeline_readiness` combines the source-corpus, schema, replay-registry, clean-rebuild, and final-acceptance views into one machine-readable state. It never executes a mutation. Its job is to answer one question: **what is the next safe operation for the current US M1.3 state?**

## Command

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\status-us-pipeline.ps1 -ExpectedHistoryParts <N> -DeepSourceTest
```

When replay is already complete, include source verification to determine whether the corpus is fully accepted:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\status-us-pipeline.ps1 -ExpectedHistoryParts <N> -DeepSourceTest -VerifySourceFiles
```

The wrapper refuses to take a deterministic snapshot while the persistent worker is running. PostgreSQL and ClickHouse must be running. A timestamped JSON report is written under `reports/` unless `-OutputPath` is supplied.

## State priority

The router evaluates states in this order:

1. **Source corpus** — source naming, SHA conflicts, ZIP/XML integrity, historical `01..N` completeness, and history/daily precedence must be safe first.
2. **Schema** — PostgreSQL and ClickHouse must both expose `US_M1.3`.
3. **Deterministic replay** — the source plan and US registry must agree, and existing successes must form a strict prefix.
4. **Clean rebuild option** — considered only when replay is blocked by a narrow set of reset-recoverable old-state conditions. It is never applied by readiness.
5. **Acceptance** — evaluated only after deterministic replay reports `COMPLETE`.
6. **Accepted** — terminal only when final acceptance returns `PASS`, including source SHA verification when requested.

## States

### `SOURCE_CORPUS_BLOCKED`

Source preflight is unsafe or incomplete. The next action is to inspect/fix the source corpus and re-run deep preflight. No database mutation is recommended.

### `SCHEMA_NOT_READY`

The source corpus is safe, but US M1.3 schema is not ready in PostgreSQL and ClickHouse. The router points to the additive/idempotent schema command.

### `STAGING_REQUIRED`

The next unfinished replay package exists only in archive. Run source staging in dry-run mode first.

### `STAGING_REQUIRED_FOR_CLEAN_REBUILD`

Old successful registry state requires a clean rebuild, but authoritative files are currently archive-only. Stage the source set before even reviewing a destructive reset plan.

### `REPLAY_READY`

The deterministic replay plan is valid and unfinished. The next command is always replay **dry-run**, never automatic apply.

### `CLEAN_REBUILD_REQUIRED`

Replay is blocked only by reset-recoverable old-state conditions and the guarded reset planner is `READY`. The next command is the clean-reset **dry-run**. Readiness never emits `-Apply` or the destructive confirmation token.

Reset-recoverable replay blockers are intentionally narrow:

- successful package profile requires M1.3 replay;
- later `SUCCESS` skipped an earlier unfinished package;
- registered source ranks violate source-plan order;
- an otherwise source-matched registry row has an unknown status.

Registry/source identity mismatches and plan-external packages are investigation states, not automatic reset candidates.

### `PIPELINE_BLOCKED`

Replay/registry integrity is blocked by a condition that should be investigated. The router does not recommend a destructive reset.

### `ACCEPTANCE_REQUIRED`

Replay is complete but no acceptance result has been evaluated in the current readiness snapshot.

### `SOURCE_VERIFICATION_REQUIRED`

Database acceptance passed with warnings because source SHA verification was not requested. Run the final source-backed acceptance audit.

### `ACCEPTANCE_NOT_READY`

Replay reports complete but acceptance evidence is still incomplete. Inspect the acceptance reasons. No automatic reset is recommended.

### `ACCEPTANCE_FAILED`

The final audit found a durable database/source integrity failure. Investigation is mandatory. Readiness deliberately does not map this state to reset.

### `ACCEPTED`

The terminal state. Replay is complete and final source-backed acceptance is `PASS`. `ready=true` and `next_action.code=NONE`.

## `next_action`

Every state includes one `next_action` object:

```json
{
  "code": "RUN_REPLAY_DRY_RUN",
  "description": "...",
  "command": "powershell.exe ...",
  "mutates": false,
  "destructive": false
}
```

The command is intentionally the safest next observation/dry-run command whenever possible. The router never emits a destructive reset apply command.

## Boundary

This readiness layer imports only read-only planners/audits. It does not apply schema, stage files, reset data, register packages, ingest packages, retry packages, or infer trademark legal conclusions.
