# Global trademark country stores

## Decision

Country trademark sources are **not** reduced to a smallest-common-denominator schema.
Each jurisdiction keeps a source-faithful store. Cross-jurisdiction search, Entity Hub and
MO Brain consume later projections; they do not replace the native stores.

Flow:

`source object -> country-native store -> country current-state projection -> global projection`

Source lineage is mandatory. `acquisition.global_trademark_source_object` records the logical
object key, SHA256 and source period without persisting host-specific absolute paths.

Dataset-level ordering/completeness is tracked separately from individual files through:

- `acquisition.global_trademark_manifest` — one source release / dataset manifest;
- `acquisition.global_trademark_manifest_object` — exact source objects attached to that manifest.

This allows a multi-object release such as the CIPO GLOBAL snapshot to express expected object
count, deterministic part sequence, source period, source precedence and predecessor/baseline
relationships without pretending that one ZIP represents the entire release.

Source catalog state has two independent dimensions:

- `active_now`: the source exists/is available or is part of the current acquisition plan.
- `pipeline_ready`: Data Engine has an implemented, tested ingestion path suitable for the stated role.

A source can therefore be `active_now=true` while `pipeline_ready=false`. This prevents the
operator/catalog from confusing source availability with production ingestion capability.

## Operator safety

The versioned global-trademark control plane is `GLOBAL_TM_SCHEMA_V2` and the operator contract
is `GLOBAL_TM_OPERATOR_V3`.

Schema migration is an explicit operator action:

```bash
python -m app.global_trademarks.cli migrate
```

Country ingestion commands are **no-write plans by default**. Database mutation requires an
explicit `--apply` flag after source preflight. The apply path registers the exact SHA-backed
source object, attaches it to a dataset manifest, and acquires a PostgreSQL advisory execution
lock so the same source scope cannot accidentally run twice on the current single-host setup.

V3 additionally binds the ingest plan to the exact path that was preflighted. The preflight SHA is
used to register the planned source object, then a one-shot source-object pin is armed. When the
loader starts, its normal source registration call must resolve to that exact object and the file is
SHA256-hashed again immediately before mutation. If the bytes changed after planning, apply fails
before country rows or ingest-run checkpoints are written.

Example:

```bash
python -m app.global_trademarks.cli ingest-ca-st96 \
  --path <file.zip> \
  --source-id CIPO_GLOBAL_2025_06_14 \
  --manifest-key CIPO_GLOBAL_2025_06_14 \
  --source-period-end 2025-06-14 \
  --source-sequence 1 \
  --source-precedence 100 \
  --expected-objects 161 \
  --part-sequence 1
```

The command above performs preflight and prints a plan only. Add `--apply` only after the plan is
accepted. Manifest attachment means the source object was received/registered; it does **not**
mean ingestion or jurisdiction acceptance succeeded.

### Bounded pilot apply

`--max-records N` caps **newly committed records in that invocation**, not the lifetime total for
the source object. Each bounded commit persists the same durable ingest checkpoint used by normal
replay. If the bound is reached before EOF, the ingest run remains `RUNNING` and the CLI reports
`status=PARTIAL`; it is intentionally not marked `COMPLETE`. A later bounded or unbounded call
resumes from the durable checkpoint.

The operator reports both `processed_rows` for the current invocation and
`cumulative_committed_rows` from the durable ingest run. `net_inserted_rows` remains unknown unless
a loader can measure inserts versus updates accurately.

## Release acceptance and Data Trust

`GLOBAL_TM_ACCEPTANCE_V2` evaluates one source release after ingestion. It is deliberately
narrower than country/jurisdiction acceptance.

The release gate requires configured pipeline readiness, country schema readiness, complete object
count/part sequence, exact source identity and SHA, the operator-declared intended pipeline, a
completed run for that exact pipeline, and resolved predecessor/baseline references. A completed
unrelated pipeline for the same source object is insufficient.

Read-only evaluation is exposed through:

```bash
python -m app.global_trademarks.cli accept-manifest \
  --manifest-id <uuid> \
  --required-coverage-through YYYY-MM-DD
```

Acceptance intentionally forces `trusted_for_silence=false`. A complete and accepted source
release does not establish that absence of an observation means legal nonexistence, invalidity,
abandonment, expiration, or any other legal/business conclusion.

Accordingly:

`ingest COMPLETE != release accepted != jurisdiction current != trusted for silence != legal conclusion`

## Jurisdiction plans

### United States

Keep the existing USPTO application/TSDR/assignment/TTAB implementation. TM-Link US is not
an ingestion source because the official data is richer and newer.

### United Kingdom

- `UKIPO_OPEN_DATA_2018`: historical thin baseline; ingestion is implemented.
- `UKIPO_COMPARABLE_RIGHTS`: source is planned but its dedicated loader is not implemented yet.
- `UKIPO_WEEKLY`: source is available but incremental current-state ingestion is not implemented.
- `UKIPO_DETAIL_PAGE`: future demand-driven enrichment only.
- TM-Link GB remains reference-only.

### European Union

- `TM_LINK_EU`: temporary historical seed only; ingestion is implemented.
- `EUIPO_API`: future official refresh/enrichment once access is operational.

TM-Link seed rows remain `current_state_verified=false` until refreshed by an official observation.

### Canada

- `CIPO_GLOBAL_2025_06_14`: authoritative ST.96 baseline. Core ingestion is durable/resumable and
  `CIPO_ST96_RICH_OBSERVATION_V1` preserves source-faithful current-owner, agent,
  representative-for-service, goods/services, office-event and registry-relationship observations.
- Rich child rows are immutable source-object observations keyed by deterministic
  `source_row_hash`; a later source object never destroys the earlier evidence.
- `CIPO_ST96_CURRENT_PROJECTION_V1` adds an explicit monotonic source-current boundary. For
  manifest-backed CIPO observations, the current winner is ordered by
  `(source_period_end, source_precedence, source_sequence)`, **not ingestion time**.
- A stale package ingested after a newer package still contributes immutable operation/child
  history but cannot regress `st96_record`, `record_state`, or the `*_current` views.
- Equal-ranked different source objects fail closed rather than resolving an ambiguous release by
  arrival time. A direct/unmanifested legacy source may continue to operate before an ordered
  winner exists, but it cannot overwrite a record once a manifest-backed ordered winner exists.
- Current views are explicit: `record_current`, `party_current`, `goods_service_current`,
  `event_current`, and `relationship_current`. They expose only the winning source object's child
  snapshot while history remains queryable in the base tables.
- A newer CIPO `Delete` wins the same order contract, sets the exact application/extension record
  source-present state false, and makes the current views empty without erasing the last full
  Update observation or any historical children.
- `CIPO_WEEKLY.pipeline_ready=false` remains deliberate. Asset/image ingestion and bounded
  validation against real CIPO GLOBAL/WEEKLY packages are still required before production-current
  promotion.
- Earlier weekly packages remain optional temporal-history replay rather than a prerequisite for
  reconstructing current state from the 2025-06-14 GLOBAL baseline plus later weekly packages.

CIPO registry relationships remain source-declared facts. Associated-mark and divisional links are
not reinterpreted inside Data Engine as a semantic brand family. A CIPO Delete is a source tombstone
for the exact application/extension record, not a legal conclusion that a trademark is invalid or
nonexistent.

### Australia

- `IPGOD_2022`: primary historical source; six-table ingestion is implemented.
- Preserve application, party activity, links, events, classification and description rather than
  flattening them.
- Party-role periods and source-declared application relationships are facts, not business
  conclusions.
- A later official source is still required for post-IPGOD freshness.

### New Zealand

- `TM_LINK_NZ`: historical thin seed; ingestion is implemented.
- `IPONZ_API`: future official update discovery and case-detail enrichment when approved access is
  confirmed usable.
- Seed rows remain unverified current state until an official observation arrives.

## Operational boundary

Large-source ingestion must use no-write source preflight, the versioned migration gate,
checksum-backed source objects, dataset manifests, explicit `--apply`, source-identity pinning,
durable/resumable acquisition, bounded pilot execution when appropriate, and separate read-only
release acceptance. None of these commands rebuild or restart the existing CN worker, enable QCC,
or authorize a jurisdiction as production-current merely because jobs complete.
