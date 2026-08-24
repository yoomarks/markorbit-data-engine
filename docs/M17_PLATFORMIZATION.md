# MarkOrbit Data Engine M1.7 — Platformization

## Status

`MARKORBIT_DATA_ENGINE_PLATFORMIZATION_M1.7_DRAFT`

M1.7 is a platformization release. It does not add a jurisdiction. Its purpose is to turn proven CN/US ingestion and replay mechanisms into reusable Data Engine primitives before WIPO, EUIPO, JPO, KIPO, UKIPO, or other jurisdictions are added.

The platformization code can be ready while the repository root remains `M1.6`: real target-host acceptance is a separate release gate and must not be inferred from static CI.

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

1. **Generic Work Engine V1** — extract durable work-unit/checkpoint state and deterministic resume from the CN final-publish implementation without changing CN behavior; prove the abstraction with a second non-CN owner.
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
- `task_key` — deterministic hash of semantic task identity within one job;
- `task_group` — logical publish/audit operation;
- `task_index` / `task_total` — operator progress metadata;
- `partition_kind` — e.g. `APPLICATION_RANGE`, `SERIAL_RANGE`, `FILE_PART`, `HASH_BUCKET`, `ENTITY_RANGE`;
- `partition_lower` / `partition_upper` — optional deterministic boundaries;
- `operation_hash` — version/hash of the executable operation;
- `status` — `RUNNING`, `SUCCESS`, or `FAILED` for V1;
- `attempts`, timestamps, and `last_error`.

V1 keeps the deterministic task-key hash compatible with the established semantic fields and scopes persistence by `(job_id, task_key)`. `job_id` is therefore an explicit durable outer scope rather than another hash input.

Resume rules:

1. A task is skippable only when the same `job_id` contains matching `task_key`, `operation_hash`, and persisted `SUCCESS` state.
2. A failed or interrupted task is rerunnable and increments `attempts`.
3. A checkpoint must validate its temporary/stage artifact identity before completed tasks can be trusted.
4. Completion is fail-closed when any work unit remains `RUNNING` or `FAILED`.
5. Temporary artifacts and progress state are deleted only after package/job success and post-publish audit.

## Compatibility rule for the first extraction

M1.7 does **not** migrate or rename the existing CN checkpoint tables. `control.cn_publish_checkpoint` and `control.cn_publish_subtask` stay authoritative for in-flight CN packages. The generic engine sits underneath the CN adapter so existing Stage checkpoints and #137 recovery state remain valid, including the established CN task-key formula.

The reusable primitive is now also used by Contact Country inference with a separate persistence adapter: `contact.country_inference_run.run_id` maps to generic `job_id`, and `ENTITY_RANGE` work units persist in `contact.country_inference_work_unit`.

This second-owner proof shows that one shared state machine does not require one shared physical table. A future `engine_*` schema remains optional and must preserve owner-specific recovery compatibility if introduced.

## Current Work Engine proof

`MARKORBIT_WORK_ENGINE_OWNER_REGISTRY_V1` is the machine-readable proof registry consumed by the platform contract and static M1.7 checkpoint.

The registered owner scopes are:

- `CN_FINAL_PUBLISH` — existing database-backed CN final-publish recovery through the Work Engine compatibility adapter;
- `CONTACT_COUNTRY_INFERENCE` — non-CN owner with PostgreSQL-backed interruption/resume, committed-result reconciliation, and membership-drift fail-closed fixtures.

The static checkpoint must block if fewer than two distinct owner scopes remain, if the second owner is no longer non-CN, or if the second-owner runtime-fixture proof disappears.

This evidence satisfies the **second-owner code/CI exit criterion only**. It explicitly sets `target_host_acceptance_claimed=false` and `release_promotion_authorized=false`. CN M1.6 target-host acceptance and Singapore authenticated retained-state acceptance remain separate gates.

## Exit criteria for M1.7

M1.7 is ready for the next jurisdiction only when:

- CN behavior and durable resume semantics remain unchanged;
- at least one existing non-CN replay/work path uses the reusable work primitives or a runtime fixture proves a second owner scope — **satisfied in code/CI by Contact Country inference**;
- no jurisdiction needs to copy the complete checkpoint/resume implementation;
- Domain Adapter Contract V1 and Data Trust/Freshness V1 are machine-readable contracts;
- Admin can report stage/work-unit progress without jurisdiction-specific SQL in the UI layer;
- CI contains interruption/resume and fail-closed audit fixtures against real PostgreSQL/ClickHouse where applicable;
- required target-host acceptance gates pass before release promotion.

Meeting an individual code/CI criterion does not by itself promote the root release version.

## Next validation jurisdictions

After M1.7 platformization and its required runtime acceptance, WIPO Madrid and EUIPO are the preferred validation pair because they stress cross-source identity, multi-designation semantics, large structured corpora, representative/owner facts, goods/services, and procedural events without resembling CN or USPTO too closely.
