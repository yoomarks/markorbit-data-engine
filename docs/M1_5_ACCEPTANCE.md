# MarkOrbit Data Engine M1.5 — China Field Model Closure

## Status

M1.5 replaces the M1.0–M1.4 development schema. It requires a clean reset of
PostgreSQL and ClickHouse development volumes. Raw ZIP files are not removed.

## Frozen rules implemented

1. `application_number` is the CN case identity, including suffixes.
2. One application number can contain multiple classes; classes do not become
   independent legal cases.
3. A `G` number is a China trademark case created through Madrid designation of
   China. It remains in the CN pipeline.
4. `G602365A` is parsed as:
   - CN case: `G602365A`
   - family root: `G602365`
   - suffix path: `A`
   - filing route: `MADRID_DESIGNATION_CN`
   - WIPO international registration number: `602365`
5. A suffix establishes a high-confidence structural `DERIVED_CASE` relation,
   but does not by itself prove the legal reason. `derivation_reason` remains
   `UNKNOWN` until supported by official evidence.
6. Year files such as `1999.zip` are filing-year base partitions, not snapshots
   whose filename year determines currentness.
7. Year-month files such as `2023_1.zip` are monthly patches and always outrank
   base partitions. Current rows use `source_rank`, not ingestion time.
8. Official facts and inferred interpretations are stored separately.
9. Numeric goods status codes `0/1/2` are preserved as `UNKNOWN`; M1.5 does not
   guess their legal meaning.
10. Raw ZIP files remain authoritative and are stored once.

## Permanent official fields

`cn_case_current` permanently stores:

- application and family numbers;
- filing route and WIPO IR link;
- filing, preliminary publication, registration publication and term dates;
- raw exclusive-period text;
- classes;
- mark type and form;
- design and color descriptions;
- exclusive-rights disclaimer;
- 3D, co-application, geographical-indication, color-mark and well-known flags;
- agent code;
- source file, logical line range and source-row hash;
- package kind, effective date and source rank.

## Goods scope semantics

`cn_case_scope_current` stores one row per case and class. Each row has:

- `source_item_count`;
- `interpreted_active_item_count`;
- `interpreted_inactive_item_count`;
- `unmapped_status_item_count`;
- `effective_item_count`, only populated when all statuses are mapped;
- `interpretation_complete`;
- mapping version and observed raw status codes;
- one compact goods payload containing sequence, similar group, name, raw
  status, normalized bucket and mapping reason.

This prevents an unmapped numeric code from being silently presented as an
active or deleted item.

## Entity baseline

M1.5 creates deterministic entity candidates only where exact matching is safe:

- owner/co-owner: exact normalized name + exact normalized address;
- agent firm: exact official agent code.

Mentions without sufficient exact evidence remain unresolved. Later fuzzy or
cross-jurisdiction merging must be a separate, auditable resolver.

## Party relations

`cn_case_party_current` separates a mention/entity from its relation to a case.
It includes:

- stable relation key and relation ID;
- current/superseded state;
- validity boundaries;
- source evidence;
- source rank.

When a later package observes a complete case-role relation set, omitted older
relations are closed with `SUPERSEDED_BY_SOURCE_OBSERVATION`. This records the
data fact without claiming whether the legal reason was assignment, name
change, address change or another procedure.

## Events

M1.5 emits explainable observed events, including:

- application/case observation;
- preliminary publication;
- registration publication;
- exclusive term and term extension;
- mark-name and agent-code changes;
- goods-scope observation/change;
- party relation observation/supersession;
- derived case observation.

Every event has source package, internal file, logical row, evidence level,
confidence and `legal_effect = NOT_DETERMINED` unless the source proves more.

## Derived cases and carve-out skeleton

- `cn_case_relation_current` stores source and target cases.
- `cn_scope_carve_out_current` stores a class-level evidence skeleton.
- The system does not claim a specific carve-out reason or goods subset until
  source/target scope evidence supports it.

## Data-quality rules

M1.5 records aggregate, idempotent issues with occurrence counts:

- invalid date chronology;
- unmapped goods status codes;
- goods without basic rows;
- basic rows without goods;
- applicants without basic rows;
- unknown headers;
- unrepairable rows;
- invalid bytes replaced.

## Acceptance sequence

1. Replace the project with the complete M1.5 package.
2. Confirm `.env` and `RAW_DATA_PATH`.
3. Run `scripts/reset-m15.ps1`.
4. Keep the worker stopped for manual validation.
5. Import `1999.zip`, `2000.zip`, then `2023_1.zip`.
6. Run `scripts/export-cn-field-audit.ps1`.
7. Check `G602365A` with `scripts/inspect-cn-case.ps1`.
8. Review unknown goods status mappings before declaring effective goods scope
   complete.
