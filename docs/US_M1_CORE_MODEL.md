# MarkOrbit US M1 Core Model

Status: OFFICIAL FACT LAYER + PACKAGE INGESTION + READ API

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

US API responses therefore expose `status_semantics = OFFICIAL_RAW_NOT_LEGAL_INTERPRETATION`.

## Package contract

The first accepted daily package convention is `apcYYMMDD.zip` / extracted XML using the same
stem. A deterministic two-digit-year pivot is frozen in code (`70-99 -> 19xx`, `00-69 -> 20xx`).
Unknown filenames have no source precedence and must not be silently ordered.

US annual/backfile packages, assignment XML, and TTAB datasets will receive separate package
contracts rather than being guessed from filename order.

## Parser and publisher contract

`app.us.parser.iter_case_bundles` uses standard-library XML `iterparse` and clears each completed
case element after it is emitted. ZIP XML members are opened as streams and are not extracted into
a persistent temporary corpus. The parser therefore scales with a case record plus publisher
batches rather than the whole daily XML document.

The parser accepts known aliases for fields that changed names across USPTO XML generations, but
US M1 fixture tests freeze the canonical durable output rather than an individual XML spelling.

The publisher assigns deterministic record identities, canonical SHA-256 record hashes, source
rank, source package UUID, source effective date, and source XML member lineage. Package
registration and job/status tracking reuse the generic PostgreSQL control plane, while US has its
own advisory ingestion lock.

Retry is full-package replay. A failed/interrupted package has all rows carrying its package UUID
removed synchronously before the authoritative registered source is parsed again. Normal
continuation is blocked while a `FAILED` or `MISSING_FILE` US package remains unresolved.

See `docs/US_M1_INGESTION.md` for operational details.

## Runtime acceptance gate

`app.us.validate_fixture` publishes two isolated records against a live ClickHouse instance:

- a direct US application with Section 1(a) filing-basis evidence;
- a Madrid 66(a) designation with an intentionally partial `20190600` first-use raw date.

It verifies all five US durable table families, direct/Madrid semantics, and the requirement that
partial dates stay typed NULL. A `finally` cleanup removes every row with the fixture package UUID
and then checks that no fixture row remains.

GitHub Actions runs this fixture with real PostgreSQL 16 and ClickHouse 24.8 containers on every PR.

## Read API

- `GET /api/us/schema`
- `GET /api/us/summary`
- `GET /api/us/cases/{serial_number}`

The API verifies that all US M1 tables exist without mutating schema state. Serial-number case
lookup only accepts exactly eight digits and returns case, owner, classification, event, and
statement facts.

## Next implementation layer

1. validate parser/publication against real USPTO daily packages;
2. add real-source data-quality profiles and acceptance reports;
3. expand backfile/annual package contracts only after real-source inspection;
4. add registration-number lookup after serial identity remains the canonical case key;
5. only then add official status-code/event interpretation and maintenance-deadline models.
