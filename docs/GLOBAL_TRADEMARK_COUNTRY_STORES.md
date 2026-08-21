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
is `GLOBAL_TM_OPERATOR_V1`.

Schema migration is an explicit operator action:

```bash
python -m app.global_trademarks.cli migrate
```

Country ingestion commands are **no-write plans by default**. Database mutation requires an
explicit `--apply` flag after source preflight. The apply path registers the exact SHA-backed
source object, attaches it to a dataset manifest, and acquires a PostgreSQL advisory execution
lock so the same source scope cannot accidentally run twice on the current single-host setup.

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

## Release acceptance and Data Trust

`GLOBAL_TM_ACCEPTANCE_V1` evaluates one source release after ingestion. It is deliberately
narrower than country/jurisdiction acceptance.

The release acceptance gate requires all of the following before `release_accepted=true`:

- the configured source pipeline is marked `pipeline_ready`;
- the country-native schema is ready;
- `expected_objects` is declared and the exact object count is attached;
- part sequences form the complete deterministic range `1..expected_objects`;
- every attached object matches the manifest jurisdiction/source identity;
- every object carries the persisted SHA256 identity created during source registration;
- every attached object has completed ingestion and none remain running or failed;
- declared predecessor and baseline manifests resolve within the same jurisdiction/source chain.

Read-only evaluation is exposed through:

```bash
python -m app.global_trademarks.cli accept-manifest \
  --manifest-id <uuid> \
  --required-coverage-through YYYY-MM-DD
```

The command projects the release evidence into the existing Data Trust dimensions:
`queryable`, `complete`, `fresh`, `accepted`, and `trusted_for_silence`.

V1 intentionally forces `trusted_for_silence=false`. A source release being complete, fresh and
accepted does **not** establish that absence of a source observation means legal nonexistence,
invalidity, abandonment, expiration, or any other legal/business conclusion. Jurisdiction-specific
contracts must separately prove any supported silence semantics before that flag can ever become
true.

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

- `CIPO_GLOBAL_2025_06_14`: authoritative ST.96 baseline. Core ingestion is implemented with
  durable batch commits and resumable checkpoints.
- `CIPO_WEEKLY`: authoritative updates and deletion stream after the baseline. Core Update/Delete
  observations and source-presence tombstones are implemented and tested. `pipeline_ready=false`
  remains deliberate until rich weekly party/goods/events/relationships/assets parsing is complete.
- Earlier weekly packages are optional historical replay, not required for current-state build.
- Preserve rich ST.96 domains: party, goods/services, events/history, registry relationships and
  assets. TM-Link CA is reference-only.

A CIPO `Delete` observation is a source tombstone for the exact application/extension record; it
is not promoted into a legal conclusion that the trademark itself is invalid or nonexistent.

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
durable/resumable acquisition, and the separate read-only release acceptance gate. None of these
commands rebuild or restart the existing CN worker, enable QCC, or authorize a new jurisdiction as
current/accepted merely because its jobs complete.
