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

The versioned global-trademark control plane is `GLOBAL_TM_SCHEMA_V1` and the operator contract
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
used to register the planned source object without an unnecessary second registration hash, then a
one-shot source-object pin is armed. When the loader starts, its normal source registration call
must resolve to that exact object and the file is SHA256-hashed again immediately before mutation.
If the path was replaced or the bytes changed after planning, apply fails before a country row or
ingest-run checkpoint is written; the loader is not allowed to silently create a second source
object for changed bytes.

Example:

```bash
python -m app.global_trademarks.cli ingest-ca-st96 \
  --path <file.zip> \
  --source-id CIPO_GLOBAL_2025_06_14 \
  --manifest-key CIPO_GLOBAL_2025_06_14 \
  --expected-objects 161 \
  --part-sequence 1
```

The command above performs preflight and prints a plan only. Add `--apply` only after the plan is
accepted. Manifest attachment means the source object was received/registered; it does **not**
mean ingestion or jurisdiction acceptance succeeded. Those remain separate evidence gates.

### Bounded pilot apply

The operator retains the V2 bounded execution control for first-contact or pilot ingestion:

```bash
python -m app.global_trademarks.cli ingest-ca-st96 \
  --path <file.zip> \
  --source-id CIPO_GLOBAL_2025_06_14 \
  --manifest-key CIPO_GLOBAL_2025_06_14 \
  --expected-objects 161 \
  --part-sequence 1 \
  --max-records 1000 \
  --apply
```

`--max-records N` caps **newly committed records in that invocation**, not the lifetime total for
the source object. Each bounded commit persists the same durable ingest checkpoint used by normal
replay. If the bound is reached before EOF, the ingest run remains `RUNNING` and the CLI reports
`status=PARTIAL`; it is intentionally **not** marked `COMPLETE`. A later bounded or unbounded call
resumes from the durable checkpoint.

The operator reports both `processed_rows` for the current invocation and
`cumulative_committed_rows` from the durable ingest run. `net_inserted_rows` remains unknown unless
a loader can measure inserts versus updates accurately; the operator does not fabricate it.

A bounded partial run cannot satisfy source-release acceptance because the attached source object
has no completed intended-pipeline run yet. If the bound happens to coincide exactly with EOF, the
first call may still remain partial rather than peeking past the safety boundary; a subsequent
resume reaches EOF and marks the durable run complete. This deliberately prefers false-incomplete
over false-complete.

## Release acceptance and Data Trust

`GLOBAL_TM_ACCEPTANCE_V2` evaluates one source release after ingestion. It is deliberately
narrower than country/jurisdiction acceptance.

The release acceptance gate requires all of the following before `release_accepted=true`:

- the configured source pipeline is marked `pipeline_ready`;
- the country-native schema is ready;
- `expected_objects` is declared and the exact object count is attached;
- part sequences form the complete deterministic range `1..expected_objects`;
- every attached object matches the manifest jurisdiction/source identity;
- every object carries the persisted SHA256 identity created during source registration;
- every object carries the operator-declared `intended_pipeline_id`;
- every attached object has a completed run for **that exact intended pipeline**, and that intended
  pipeline is neither running nor failed;
- declared predecessor and baseline manifests resolve within the same jurisdiction/source chain.

A completed unrelated pipeline for the same source object does not satisfy acceptance. This closes
the gap where generic "some complete run exists" evidence could otherwise be mistaken for proof
that the operator-declared loader/domain actually completed.

Read-only evaluation is exposed through:

```bash
python -m app.global_trademarks.cli accept-manifest \
  --manifest-id <uuid> \
  --required-coverage-through YYYY-MM-DD
```

The command projects the release evidence into the existing Data Trust dimensions:
`queryable`, `complete`, `fresh`, `accepted`, and `trusted_for_silence`.

Acceptance intentionally forces `trusted_for_silence=false`. A source release being complete,
fresh and accepted does **not** establish that absence of a source observation means legal
nonexistence, invalidity, abandonment, expiration, or any other legal/business conclusion.
Jurisdiction-specific contracts must separately prove any supported silence semantics before that
flag can ever become true.

Accordingly:

`ingest COMPLETE != release accepted != jurisdiction current != trusted for silence != legal conclusion`

Historical-seed releases can pass structural release acceptance while still carrying warnings that
the source is historical and current state is not verified. Likewise, an authoritative baseline can
pass release acceptance without proving that later incremental packages have been applied through
today.

## Jurisdiction plans

### United States

Keep the existing USPTO application/TSDR/assignment/TTAB implementation. TM-Link US is not
an ingestion source because the official data is richer and newer.

### United Kingdom

- `UKIPO_OPEN_DATA_2018`: historical thin baseline from domestic and Madrid-to-UK TXT files;
  ingestion is implemented.
- `UKIPO_COMPARABLE_RIGHTS`: separate Brexit comparable-right population and relationships;
  source is part of the plan but its dedicated loader is not implemented yet.
- `UKIPO_WEEKLY`: rich incremental observations/events; source is available but its incremental
  current-state pipeline is not implemented yet.
- `UKIPO_DETAIL_PAGE`: future demand-driven enrichment only; do not bulk crawl around
  human-verification controls.
- TM-Link GB is reference-only because it derives from the thinner 2018 source.

### European Union

- `TM_LINK_EU`: temporary historical seed only; ingestion is implemented.
- `EUIPO_API`: future official refresh/enrichment once access is operational.

TM-Link seed rows must remain `current_state_verified=false` until refreshed by an official
observation. The native EU schema is intentionally allowed to grow beyond TM-Link fields.

### Canada

- `CIPO_GLOBAL_2025_06_14`: authoritative ST.96 baseline. Core ingestion is durable/resumable and
  `CIPO_ST96_RICH_OBSERVATION_V1` now preserves source-faithful child observations for the current
  owner, trademark agent, representative for service, goods/services statements and Nice classes,
  office actions/events, previous-associated/divisional application links, and national associated
  marks.
- Rich child rows are **immutable source-object observations**, keyed by deterministic
  `source_row_hash`. They are not a source-current table. A later weekly Update contributes a new
  source-object-specific child snapshot and leaves the earlier observation intact for history and
  provenance.
- `CIPO_WEEKLY`: Update/Delete observations, exact application/extension source-presence
  tombstones, and the same rich child observation extraction are implemented. A Delete does not
  insert empty replacement children and never erases previously observed party/goods/event/
  relationship evidence.
- `CIPO_WEEKLY.pipeline_ready=false` remains deliberate. Before the weekly source is promoted to a
  production-current pipeline, Data Engine still needs an ordered source-current projection that
  applies baseline/predecessor/weekly precedence explicitly, plus asset/image ingestion and real
  package shape/performance validation.
- Earlier weekly packages remain optional temporal-history replay rather than a prerequisite for
  reconstructing current state from the 2025-06-14 GLOBAL baseline plus later weekly packages.
- TM-Link CA remains reference-only because official CIPO ST.96 is richer and newer.

CIPO's relationship structures are retained as source-declared registry facts. In particular,
associated-mark and divisional/previous-application links must not be reinterpreted inside Data
Engine as a semantic brand family. Likewise, a CIPO `Delete` observation is only a source tombstone
for the exact application/extension record; it is not a legal conclusion that the trademark itself
is invalid or nonexistent.

### Australia

- `IPGOD_2022`: primary historical source; six-table ingestion is implemented.
- Preserve the source's six domains rather than flattening them: application, party activity,
  application links, application events, classification and description.
- Party role effective periods and source-declared application relationships are facts, not
  business conclusions.
- Images and goods/services can be enriched from later sources without redesigning the store.
- TM-Link AU is reference-only because it derives from older IPGOD data.

### New Zealand

- `TM_LINK_NZ`: historical thin seed; ingestion is implemented.
- `IPONZ_API`: future official update discovery and case-detail enrichment when the approved
  API access is confirmed usable.
- Seed rows remain unverified current state until an official observation arrives.

## Operational boundary

Large-source ingestion must use no-write source preflight, the versioned migration gate,
checksum-backed source objects, dataset manifests, the explicit `--apply` operator boundary,
source-identity pinning, durable/resumable acquisition, bounded pilot execution for cautious first
runs when appropriate, and the separate read-only release acceptance gate. None of these commands
rebuild or restart the existing CN worker, enable QCC, or authorize a new jurisdiction as
current/accepted merely because its jobs complete.
