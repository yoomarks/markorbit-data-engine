# MarkOrbit Operations V2

`MARKORBIT_OPERATIONS_V2`

Operations V2 is the read-only recovery and operator-decision layer for Data Engine. It does not replace package ingestion, transition gates, checkpoint validators, or Admin task mutation handlers. It makes their state understandable in one place.

## Problem

Before Operations V2, recovery semantics were correct but distributed across several places:

- `control.source_package` knew package state;
- `control.job_run` knew queue/run/failure state;
- CN Stage Resume knew whether raw ZIP parsing could be skipped;
- CN Final Publish knew which bounded work units had succeeded or failed;
- Admin Domain Tasks knew cooperative stop/restart behavior;
- domain transition gates decided whether mutation was allowed.

A human or future agent could therefore see `FAILED` without knowing whether it meant:

1. replay the package from the raw source;
2. resume after Stage;
3. resume only the failed final-publish work unit;
4. wait because work is still running;
5. restore a missing source file;
6. stop and inspect an orphan processing state.

Operations V2 makes those distinctions explicit.

## State model

The common state vocabulary is:

- `READY` — registered work can start through its existing domain gate;
- `QUEUED` — already waiting for a worker;
- `RUNNING` — active work; do not launch a competing mutation;
- `STOPPING` — cooperative stop requested; wait for the safe boundary;
- `PAUSED` — cooperative stop completed and may later continue through the gate;
- `RESUME_CANDIDATE` — durable checkpoint exists, but must be verified before resume;
- `RETRY_CANDIDATE` — no durable resume checkpoint; verify source and gates before replay;
- `BLOCKED` — a prerequisite such as the registered source file is missing;
- `NEEDS_OPERATOR` — state is internally inconsistent or cannot be resolved safely by metadata;
- `COMPLETE` — accepted package/task success;
- `IDLE` — no actionable operation state.

## Checkpoint rule

Checkpoint presence is **not** proof that resume is safe.

For CN Stage Resume, the existing validator still compares the durable checkpoint against exact ClickHouse Stage counts and source identity. For CN Final Publish, the existing validator still compares exact publish-stage counts and checkpoint version before any work unit is continued.

Operations V2 therefore reports actions such as:

```text
VERIFY_STAGE_CHECKPOINT_THEN_RESUME_POST_STAGE
VERIFY_FINAL_CHECKPOINT_THEN_CONTINUE_WORK_UNIT
```

It never reports an unchecked checkpoint as already safe.

## Partial-state preservation

If a resumable checkpoint exists, failure must preserve the checkpoint and its temporary computation state. Successfully completed work units must not be replayed. Cleanup belongs after package-level success, not after a resumable failure.

This is the generic form of the recovery architecture proven by CN PR #137:

```text
materialize temporary state
        ↓
persist checkpoint
        ↓
execute bounded durable work units
        ↓
mark each SUCCESS / FAILED
        ↓
restart skips SUCCESS
        ↓
audit final state
        ↓
package SUCCESS
        ↓
cleanup checkpoint + temporary state
```

## Live admin view

The local admin API exposes:

```text
GET /api/admin/v2/system/operations
```

The view currently combines:

- source-package state;
- latest package job state;
- Admin Domain Task state;
- CN Stage checkpoint presence;
- CN Final Publish checkpoint presence;
- CN final-publish work-unit counts and earliest unfinished task index.

The endpoint is read-only.

## Mutation authority

Every suggested `next_safe_action` is advisory. Existing domain transition gates, source verification, storage-headroom gates, exact checkpoint validators, and worker locking remain authoritative.

Operations V2 does not authorize legal conclusions, filing actions, Matter changes, client communication, or business workflow.

## Extension rule

New jurisdictions do not create new global operation-state semantics. They add evidence adapters that map their own replay/checkpoint/acceptance state into this stable vocabulary. This keeps operator tooling and future autonomous recovery behavior consistent while leaving jurisdiction-specific safety gates intact.
