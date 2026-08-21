# Global trademark country stores

## Decision

Country trademark sources are **not** reduced to a smallest-common-denominator schema.
Each jurisdiction keeps a source-faithful store. Cross-jurisdiction search, Entity Hub and
MO Brain consume later projections; they do not replace the native stores.

Flow:

`source object -> country-native store -> country current-state projection -> global projection`

Source lineage is mandatory. `acquisition.global_trademark_source_object` records the logical
object key, SHA256 and source period without persisting host-specific absolute paths.

## Jurisdiction plans

### United States

Keep the existing USPTO application/TSDR/assignment/TTAB implementation. TM-Link US is not
an ingestion source because the official data is richer and newer.

### United Kingdom

- `UKIPO_OPEN_DATA_2018`: historical thin baseline from domestic and Madrid-to-UK TXT files.
- `UKIPO_COMPARABLE_RIGHTS`: separate Brexit comparable-right population and relationships.
- `UKIPO_WEEKLY`: rich incremental observations/events; gradually thickens older records.
- `UKIPO_DETAIL_PAGE`: future demand-driven enrichment only; do not bulk crawl around
  human-verification controls.
- TM-Link GB is reference-only because it derives from the thinner 2018 source.

### European Union

- `TM_LINK_EU`: temporary historical seed only.
- `EUIPO_API`: future official refresh/enrichment once access is operational.

TM-Link seed rows must remain `current_state_verified=false` until refreshed by an official
observation. The native EU schema is intentionally allowed to grow beyond TM-Link fields.

### Canada

- `CIPO_GLOBAL_2025_06_14`: authoritative ST.96 baseline.
- `CIPO_WEEKLY`: authoritative updates and deletion stream after the baseline.
- Earlier weekly packages are optional historical replay, not required for current-state build.
- Preserve rich ST.96 domains: party, goods/services, events/history, registry relationships and
  assets. TM-Link CA is reference-only.

### Australia

- `IPGOD_2022`: primary historical source.
- Preserve the source's six domains rather than flattening them: application, party activity,
  application links, application events, classification and description.
- Party role effective periods and source-declared application relationships are facts, not
  business conclusions.
- Images and goods/services can be enriched from later sources without redesigning the store.
- TM-Link AU is reference-only because it derives from older IPGOD data.

### New Zealand

- `TM_LINK_NZ`: historical thin seed.
- `IPONZ_API`: future official update discovery and case-detail enrichment when the approved
  API access is confirmed usable.
- Seed rows remain unverified current state until an official observation arrives.

## Operational safety

Schema installation is additive and does not start acquisition:

```bash
python -m app.global_trademarks.cli catalog
python -m app.global_trademarks.cli migrate
```

`migrate` creates only the new PostgreSQL schemas/tables. It does not rebuild or restart the
existing CN worker, does not enable QCC, and does not download or ingest any real country data.
Large source ingestion must be introduced per country with fixture validation and resumable,
checksum-backed acquisition before production execution.
