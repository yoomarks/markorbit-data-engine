# MarkOrbit Data Engine — Cognitive Platform Alignment

Status: Proposed alignment baseline

This document aligns Data Engine with the MarkOrbit cognitive-platform architecture used by Core/Brain/Capability and Knowledge.

## 1. Data Engine responsibility

Data Engine owns objective structured facts and historical factual state.

The governing boundary is:

> Knowledge owns documents. Data Engine owns facts. Brain owns methods. Capability owns execution. Products own business state.

Data Engine is the large factual substrate. It should stay stable, queryable, reproducible, and intentionally conservative about interpretation.

## 2. Facts Data Engine should own

Examples include:

- trademark application and registration records;
- applicant/owner/entity records;
- prosecution/case/proceeding records;
- assignment and ownership events;
- trademark status and lifecycle events;
- dates and event timelines;
- factual entity identifiers;
- recorded addresses and contact attributes where legitimately sourced;
- historical snapshots/change feeds;
- source-backed factual relationships.

## 3. What Data Engine should not own

Data Engine must not persist AI/analytical conclusions as source truth merely because higher layers compute them.

Examples that belong outside Data Engine source truth:

- customer opportunity classification;
- renewal propensity;
- expansion propensity;
- dead-mark commercial attractiveness;
- application risk score;
- inferred same-group probability;
- inferred holding-company classification;
- recommendation/ranking decisions;
- product campaign state.

If a relationship later becomes an independently sourced fact, it may enter Data Engine through the normal factual-ingestion contract. Inferred relationships remain method outputs or product state until then.

## 4. Data Engine as analytical substrate

Although intelligence conclusions belong in Brain methods and Capability execution, Data Engine must expose efficient primitives so those methods do not need to copy large populations elsewhere.

Required long-term primitives include:

- scoped filtering and projection;
- deterministic pagination/streaming;
- joins across trademarks, entities, cases, assignments, and events;
- grouped aggregation;
- count/distinct count;
- time-window queries;
- time-series bucketing;
- duration calculations over factual event timelines;
- status-transition extraction;
- historical snapshot access;
- reproducible query/dataset identity;
- bounded sampling for research/backtesting;
- feature-source extraction without embedding business interpretation in Data Engine.

Where performance requires precomputation, prefer factual/materialized query infrastructure whose semantics remain objective. Do not encode business scoring policy into those tables.

## 5. Brain research interaction

Brain may use Data Engine populations to discover and validate reusable methods.

Examples:

- Entity Resolution Method;
- Relationship Inference Method;
- Examination-Time Analysis Method;
- Application Risk Method;
- Renewal Opportunity Method;
- Jurisdiction Expansion Opportunity Method;
- Dead Trademark Opportunity Method;
- Status Transition Method.

Brain may sample, aggregate, cluster, train, and backtest against Data Engine data, but raw source populations must not be copied into Brain as long-term ownership.

A Brain method must retain reproducible lineage to the Data Engine query/snapshot or dataset definition used for research and evaluation.

## 6. Capability execution interaction

Capabilities may execute ACTIVE Brain methods against Data Engine facts.

Examples:

- `assess_application_risk`;
- `resolve_entity`;
- `detect_same_group`;
- `find_renewal_opportunities`;
- `find_expansion_opportunities`;
- `trademark_volume_statistics`;
- `status_transition_statistics`;
- `estimate_examination_time`.

Data Engine returns facts or objective aggregates. Capability owns method execution. Product consumers own durable business lifecycle state.

## 7. Candidate and cache rule

Business-candidate result sets must not become Data Engine intelligence tables merely to simplify downstream workflows.

For example, a list of 100,000 renewal opportunities belongs to the consuming MarkReg opportunity pool after import, not to Data Engine or Brain.

Data Engine may support the factual query used to generate those candidates and may retain ordinary source/history data required to recompute them.

## 8. Long-term development obligations

### DE-CG-A — analytical primitive inventory

Audit current query APIs and identify gaps for:

- time-series statistics;
- duration statistics;
- status-transition analysis;
- entity/trademark relationship joins;
- bounded research sampling;
- reproducible dataset/query snapshots.

### DE-CG-B — reproducible dataset contract

Define a contract allowing Brain research and Capability execution to identify the exact factual scope used for a method result or backtest.

The contract should support method lineage without copying the population into Brain.

### DE-CG-C — high-cost factual aggregation support

Add only objective, reusable aggregation primitives that materially reduce repeated scanning of large populations.

Examples:

- event-duration primitives;
- grouped counts by jurisdiction/class/status/time bucket;
- status-transition matrices;
- portfolio membership lookup;
- ownership/assignment traversal based on recorded facts.

Do not add opportunity, risk, or recommendation policy to these primitives.

### DE-CG-D — source-truth boundary tests

Add documentation or architecture tests ensuring inferred intelligence does not silently become factual source truth.

## 9. Exit criteria for Data Engine cognitive readiness

Data Engine is cognitive-platform-ready when:

- Brain can perform real research/backtests without bulk-copying source populations;
- Capability can execute scoped analytical methods efficiently;
- large statistical workloads have objective reusable query support;
- every research/evaluation dataset can be reproduced from a query/snapshot identity;
- inferred intelligence remains outside factual source truth.
