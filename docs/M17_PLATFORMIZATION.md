# MarkOrbit Data Engine M1.7 — Platformization

## Status

`MARKORBIT_DATA_ENGINE_PLATFORMIZATION_M1.7_DRAFT`

M1.7 is a platformization release. It does not add a jurisdiction. Its purpose is to turn proven CN/US ingestion and replay mechanisms into reusable Data Engine primitives before WIPO, EUIPO, JPO, KIPO, UKIPO, or other jurisdictions are added.

## Product boundary

Data Engine remains the authoritative **Source Fact Service**. Platformization must not move legal reasoning, Matter/workflow state, customer state, or business execution into Data Engine.

The reusable engine owns:

- source registration and verification;
- deterministic stage/publish work;
- durable checkpoints and work-unit progress;
- idempotent resume after interruption;
- source-fact publication;
- evidence/lineage preservation;
- bounded audits and formal acceptance;
- operational progress and failure telemetry.

Jurisdiction adapters own only source-specific parsing, identity, mapping, semantics guards, and acceptance rules that cannot be generalized safely.

## M1.7 workstreams

1. **Generic Work Engine V1** — extract durable work-unit/checkpoint state and deterministic resume from the CN final-publish implementation without changing CN behavior.
2. **Domain Adapter Contract V1** — freeze discover/register/verify/parse/stage/normalize/publish/event/audit/accept lifecycle hooks.
3. **Native Publish DAG** — replace legacy SQL-shape interception progressively with explicit task definitions, dependencies, partition strategies, idempotency rules, and audits.
4. **Global Fact/Event Envelope** — normalize provenance and event-family metadata while preserving jurisdiction-specific source semantics and `legal_conclusion=false` boundaries.
5. **Data Trust/Freshness V1** — expose coverage, completeness, freshness, acceptance, and trusted-for-silence status to consumers.
6. **Operations V2** — expose durable stage/work-unit progress and recovery state in Admin.

## Generic Work Engine V1 contract

A reusable work unit is identified by stable inputs, not by process lifetime.

Required fields:

- `owner_scope` — jurisdiction/domain/component that owns the work;
- `job_id` — durable source package or job identity;
- `checkpoint_version` — semantic version of the work plan;
- `task_key` — deterministic hash of semantic task identity;
- `task_group` — logical publish/audit operation;
- `task_index` / `task_total` — operator progress metadata;
- `partition_kind` — e.g. `APPLICATION_RANGE`, `SERIAL_RANGE`, `FILE_PART`, `HASH_BUCKET`, `ENTITY_RANGE`;
- `partition_lower` / `partition_upper` — optional deterministic boundaries;
- `operation_hash` — version/hash of the executable operation;
- `status` — `RUNNING`, `SUCCESS`, or `FAILED` for V1;
- `attempts`, timestamps, and `last_error`.

Resume rules:

1. A task is skippable only when `task_key` and `operation_hash` match a persisted `SUCCESS` record.
2. A failed or interrupted task is rerunnable and increments `attempts`.
3. A checkpoint must validate its temporary/stage artifact identity before completed tasks can be trusted.
4. Completion is fail-closed when any work unit remains `RUNNING` or `FAILED`.
5. Temporary artifacts and progress state are deleted only after package/job success and post-publish audit.

## Compatibility rule for the first extraction

The first M1.7 slice must **not** migrate or rename the existing CN checkpoint tables. `control.cn_publish_checkpoint` and `control.cn_publish_subtask` stay authoritative for in-flight CN packages. The generic engine is introduced underneath the CN adapter so existing Stage checkpoints and #137 recovery state remain valid.

Only after the generic contract is proven by CN runtime fixtures may a later migration introduce shared `engine_*` control tables.

## Exit criteria for M1.7

M1.7 is ready for the next jurisdiction only when:

- CN behavior and durable resume semantics remain unchanged;
- at least one existing non-CN replay path uses the reusable work primitives or a runtime fixture proves a second owner scope;
- no jurisdiction needs to copy the complete checkpoint/resume implementation;
- Domain Adapter Contract V1 and Data Trust/Freshness V1 are machine-readable contracts;
- Admin can report stage/work-unit progress without jurisdiction-specific SQL in the UI layer;
- CI contains interruption/resume and fail-closed audit fixtures against real PostgreSQL/ClickHouse where applicable.

## Next validation jurisdictions

After M1.7 platformization, WIPO Madrid and EUIPO are the preferred validation pair because they stress cross-source identity, multi-designation semantics, large structured corpora, representative/owner facts, goods/services, and procedural events without resembling CN or USPTO too closely.
