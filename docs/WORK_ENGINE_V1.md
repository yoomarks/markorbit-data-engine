# MarkOrbit Generic Work Engine V1

`MARKORBIT_WORK_ENGINE_V1`

The Work Engine provides durable, idempotent, resumable execution semantics for large Data Engine jobs. It is intentionally jurisdiction-neutral.

## Stable work identity

V1 separates the durable **job scope** from the deterministic **task key**.

A job is identified by:

```text
job_id
```

Within that job, the deterministic `task_key` is derived from semantic task inputs:

```text
owner_scope
checkpoint_version
operation_hash
partition_kind
partition_lower
partition_upper
```

The V1 `task_key` is a SHA-256 over those fields and the Work Engine version. `job_id` is deliberately **not** added to that hash: established V1 task-key semantics remain stable, while the full persistence identity is:

```text
(job_id, task_key)
```

Therefore a task key is job-local. Two jobs may legitimately have the same task key without sharing durable state.

The higher-level work-unit identity exposed by the machine contract is:

```text
owner_scope
job_id
checkpoint_version
task_key
```

## Job-scoped persistence callbacks

`DurableWorkUnitStore` requires a non-empty `job_id` and passes it explicitly through persistence callbacks:

```text
read_task(job_id, task_key)
set_success(job_id, task_key)
set_failed(job_id, task_key, error)
summarize(job_id)
```

`upsert_running(...)` receives a `WorkUnitSpec` that carries the same `job_id`.

This prevents an adapter from accidentally collapsing durable state for two jobs that share the same V1 task key. A domain may map the generic `job_id` onto an existing physical identifier without changing its table schema; CN maps it to `package_id`, while Contact Country inference maps it to `country_inference_run.run_id`.

## Resume semantics

A prior unit may be skipped only if:

```text
status == SUCCESS
and persisted operation_hash == requested operation_hash
```

inside the same `job_id` scope.

A `FAILED` or interrupted `RUNNING` unit is not considered complete. Retrying writes `RUNNING` again and the persistence adapter is responsible for incrementing durable attempts.

## Completion semantics

Completion fails closed while any work unit remains `RUNNING` or `FAILED` in the current job.

A domain-level checkpoint must separately verify that temporary/stage artifacts still match the checkpoint before previously completed work is trusted. The Work Engine does not pretend a task ledger can validate data artifacts by itself.

## Partition strategies

V1 reserves common partition kinds:

- `APPLICATION_RANGE`
- `SERIAL_RANGE`
- `FILE_PART`
- `HASH_BUCKET`
- `ENTITY_RANGE`
- `AGENT_CODE_BATCH`
- `CUSTOM`

A jurisdiction or domain may choose the semantic boundary that preserves its identity and aggregation rules. The platform must not split a semantic entity merely to hit a row target.

## Proven owner scopes

`MARKORBIT_WORK_ENGINE_OWNER_REGISTRY_V1` records the concrete adapters proven against this contract.

1. **CN final publish** — `CN_FINAL_PUBLISH`; existing `package_id` is the generic `job_id`, while `control.cn_publish_subtask` and the legacy CN V1 task-key formula remain compatible for in-flight recovery state.
2. **Contact Country inference** — `CONTACT_COUNTRY_INFERENCE`; `contact.country_inference_run.run_id` is the generic `job_id`, and ordered `ENTITY_RANGE` units persist in `contact.country_inference_work_unit`.

The Contact Country runtime workflow includes PostgreSQL-backed fixtures for interruption/resume, the crash window where result rows committed before the SUCCESS ledger transition, and membership-drift fail-closed behavior. This satisfies the M1.7 requirement for a genuine second non-CN owner of the reusable primitive.

This registry is **static code/CI evidence only**. It does not claim that CN or Singapore target-host acceptance has passed and does not authorize release promotion.

## Persistence boundary

V1 deliberately uses callback-backed persistence rather than forcing one shared table. CN keeps `control.cn_publish_checkpoint` / `control.cn_publish_subtask` valid for in-flight packages, while Contact Country uses its own durable work-unit table.

The second-owner proof demonstrates that the shared abstraction does not require a shared physical table. A future `engine_*` schema is an optional storage consolidation decision, not an M1.7 correctness requirement; it must preserve owner-specific compatibility and recovery evidence if introduced.

## Non-goals

The Work Engine does not define trademark facts, legal status, legal outcomes, workflow tasks, Matter state, customer state, or legal advice. `legal_conclusion=false` remains a permanent boundary.
