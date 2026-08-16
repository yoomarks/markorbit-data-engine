# MarkOrbit Generic Work Engine V1

`MARKORBIT_WORK_ENGINE_V1`

The Work Engine provides durable, idempotent, resumable execution semantics for large Data Engine jobs. It is intentionally jurisdiction-neutral.

## Stable work identity

A work unit is keyed by semantic inputs rather than process lifetime:

```text
owner_scope
checkpoint_version
operation_hash
partition_kind
partition_lower
partition_upper
```

The deterministic `task_key` is a SHA-256 over those fields and the Work Engine version.

## Resume semantics

A prior unit may be skipped only if:

```text
status == SUCCESS
and persisted operation_hash == requested operation_hash
```

A `FAILED` or interrupted `RUNNING` unit is not considered complete. Retrying writes `RUNNING` again and the persistence adapter is responsible for incrementing durable attempts.

## Completion semantics

Completion fails closed while any work unit remains `RUNNING` or `FAILED`.

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

A jurisdiction may choose the semantic boundary that preserves its identity and aggregation rules. The platform must not split a semantic entity merely to hit a row target.

## Persistence boundary

V1 deliberately uses callback-backed persistence rather than forcing shared tables immediately. This allows CN's existing `control.cn_publish_checkpoint` / `control.cn_publish_subtask` state to remain valid for in-flight packages while CN is refactored onto the shared state machine.

A later migration may introduce shared `engine_*` tables only after compatibility and runtime fixtures prove the abstraction across more than one owner scope.

## Non-goals

The Work Engine does not define trademark facts, legal status, legal outcomes, workflow tasks, Matter state, customer state, or legal advice. `legal_conclusion=false` remains a permanent boundary.
