# MarkOrbit US M1 Core Model

Status: FOUNDATION / OFFICIAL FACT LAYER

US M1 starts from USPTO Trademark Daily Applications XML (TDXF) materialized under
`raw_data/incoming/us`. It deliberately separates official USPTO facts from later legal-status
interpretation, maintenance calculations, and product recommendations.

## Source boundary

The primary continuous source is the USPTO Trademark Daily Applications XML feed. USPTO's
Office of the Chief Economist Trademark Case Files Dataset and its official variable tables are
used as a field-model cross-check because that dataset is derived from the raw trademark XML.

The legacy Bulk Data Storage System has moved to the USPTO Open Data Portal. Authentication and
download orchestration are not part of US M1 core: official files are first materialized locally,
then the data engine performs deterministic hashing, parsing, and publication. This keeps source
acquisition credentials outside the parser/publisher contract.

## Durable identity

- `serial_number` is the primary US case identity.
- `registration_number` is an attribute and secondary lookup key; it is not a replacement for
  serial identity.
- Owner, classification, event, and statement identities are subordinate to the serial number.
- Daily source precedence is derived from the source update date, not ingestion wall-clock time.

## US M1 tables

### `us_case_current`

Stores the current official case observation: filing/publication/registration dates, raw USPTO
status code/date, mark identification/drawing code, filing-basis flags, and Madrid fields.

### `us_owner_current`

Stores durable owner observations including entry number, party type, legal-entity code,
nationality, and postal address. US M1 does not yet decide which historical party-type row should
be presented as the sole legal owner; that selection requires an independently tested lifecycle
contract.

### `us_classification_current`

Stores the primary class, International/US class arrays, class status, and first-use evidence.
USPTO historical XML may contain partial dates such as `YYYYMM00`. Those values remain in the raw
columns while the typed Date32 value stays NULL. The engine must never invent the missing day.

### `us_event_history`

Stores observed USPTO event code/date/sequence/type. Events are evidence, not pre-labeled legal
conclusions.

### `us_statement_current`

Stores USPTO statement type and text. This includes goods/services statements, disclaimers, mark
descriptions, translations, and other statement families without flattening their type codes.

## Status boundary

US M1 preserves official `status_code` and `status_date`. It does **not** create an `ACTIVE`,
`DEAD`, `REGISTERED`, `ABANDONED`, Section 8, Section 15, or renewal conclusion from the code alone.
Those product/legal semantics will live in a versioned interpretation layer with explicit rule
IDs and official-event evidence.

## Package contract

The first accepted daily package convention is `apcYYMMDD.zip` / extracted XML using the same
stem. A deterministic two-digit-year pivot is frozen in code (`70-99 -> 19xx`, `00-69 -> 20xx`).
Unknown filenames have no source precedence and must not be silently ordered.

US annual/backfile packages, assignment XML, and TTAB datasets will receive separate package
contracts rather than being guessed from filename order.

## Parser contract

`app.us.parser.iter_case_bundles` uses standard-library XML `iterparse` and clears each completed
case element after it is emitted. The parser therefore scales with a case record rather than the
whole daily XML document.

The parser accepts known aliases for fields that changed names across USPTO XML generations, but
US M1 fixture tests freeze the canonical durable output rather than an individual XML spelling.

## Next implementation layer

After this foundation is green:

1. publish US bundles into ClickHouse with deterministic hashes and source lineage;
2. add US package registration/ingest/retry guards using the generic PostgreSQL control plane;
3. add `/api/us/summary` and `/api/us/cases/{serial_number}`;
4. validate against real USPTO daily packages;
5. only then add official status-code/event interpretation and maintenance-deadline models.
