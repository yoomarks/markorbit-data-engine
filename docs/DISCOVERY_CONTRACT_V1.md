# Data Engine Discovery Contract V1

Tracking: Data Engine #348, Core `yoomarks/markorbit#312`.

## Status

This document freezes only the business-neutral safety contract required before a real Phase 4 Discovery stream can be implemented.

It does **not** authorize a production Discovery endpoint, a CN/US/SG replay, a population scan, a scoring method, or candidate lifecycle storage.

Core #312 still owns the precursor gate for the first real stream: one concrete discovery question, one candidate type, exact source fields/query scope, and product ownership for imported/reviewed candidates must be frozen before Data Engine binds this contract to serving tables.

## V1 contracts

`app.discovery_contract` defines:

- `DATA_ENGINE_DISCOVERY_CONTRACT_V1` query/page provenance semantics;
- `DATA_ENGINE_DISCOVERY_CURSOR_V1` continuation cursor semantics;
- deterministic query identity over stream, source schema, candidate type, projection, scope and hard bounds;
- snapshot references that bind an opaque snapshot ID to a kind, serving watermark and source version;
- bounded keyset continuation state;
- fail-closed validation for query hash, snapshot ID, cursor structure, cursor checksum and page/result bounds.

The module is pure contract code. It does not import FastAPI, PostgreSQL or ClickHouse and has no persistence/cache.

## Deterministic query identity

A V1 query identity contains exactly:

- `contract_version`;
- `stream_id`;
- `source_schema_id`;
- `candidate_type`;
- ordered `projection_fields`;
- `scope`;
- `limits` (`page_size`, `max_pages`, `max_results`);
- `query_hash`.

`query_hash` is SHA-256 over canonical JSON for all fields above except the hash itself. Object keys are sorted, so mapping key order does not change identity. Projection order does matter because it is part of the output contract.

V1 query identity deliberately excludes floating-point JSON values. Scope values must use strings, integers, booleans, null, arrays or objects. Dates/decimal values should be represented by a stream's frozen string representation. This avoids ambiguous cross-runtime identity rules before a real stream contract exists.

Changing scope, projected fields, candidate type, source schema or limits produces a different query identity. A cursor produced for the old identity must fail closed against the new one.

## Absolute safety ceilings

The generic layer provides only upper ceilings; a real stream may and normally should choose tighter limits:

- page size: at most 200;
- pages: at most 100;
- results: at most 10,000.

These ceilings are not permission to scan 10,000 rows. The first real stream must freeze its own smaller bounds appropriate to the accepted discovery question.

## Cursor semantics

The cursor is an opaque URL-safe token containing:

- cursor contract version;
- exact query hash;
- exact snapshot ID;
- ordered keyset position scalars;
- next page number;
- already emitted result count;
- deterministic checksum over the cursor payload.

A cursor is valid only for the same query identity and the same snapshot ID. Decode fails closed when:

- the token/envelope/payload is malformed;
- checksum verification fails;
- contract version differs;
- query identity differs;
- snapshot differs;
- the keyset position is invalid;
- the next page or emitted count exceeds the frozen query bounds.

The checksum is corruption/tamper detection, not an authorization mechanism. Authorization and allowed scope must be enforced independently by the eventual API/runtime. A client-controlled cursor must never broaden an authorized query scope.

## Snapshot semantics

V1 does not invent a database snapshot implementation. A real stream must provide one immutable/replayable snapshot or serving-watermark contract and bind it into:

- `snapshot_id`;
- `snapshot_kind`;
- `watermark`;
- `source_version`.

The cursor binds to `snapshot_id`. Page provenance carries the full snapshot reference. The real stream acceptance must prove that the selected watermark produces stable pagination/replay for the accepted evidence window.

## Page provenance

Every emitted page can be revalidated through `build_page_provenance`. It records:

- exact query hash and query contract fields;
- exact snapshot reference;
- Data Engine version;
- page number;
- result count;
- cumulative emitted count;
- whether continuation exists.

If continuation exists, the page builder decodes it again and requires the cursor to point to exactly the next page with the same cumulative count.

Per-result source references are intentionally **not** invented in this generic contract. Their exact primary/source keys depend on the candidate type and serving schema selected by Core #312. The first real stream must add deterministic result-level source identity without copying review/import/conversion state into Data Engine.

## Existing Data Engine reuse/gap matrix

| Area | Existing state | V1 decision |
| --- | --- | --- |
| Bounded page size | `app/admin_paging_api.py` caps admin pages at 200 | Reuse the safety principle only |
| Pagination | Admin API uses `LIMIT/OFFSET` | Do not reuse for production Discovery; real stream must use deterministic keyset ordering |
| Query identity | No accepted Phase 4 Discovery identity | Added business-neutral canonical query hash |
| Snapshot binding | Serving/checkpoint concepts exist per domain | Real stream must freeze one accepted snapshot/watermark; V1 only defines the reference envelope |
| Cursor mismatch handling | No Phase 4 cursor contract | Added fail-closed query/snapshot/bounds validation |
| Candidate lifecycle | Product concern | Must remain outside Data Engine Discovery runtime |
| Ranking/scoring | Core #311 precursor required | Explicitly excluded from #348 contract work |

## Gate to real implementation

Do not add the real endpoint/ClickHouse query until Core #312 freezes all of the following:

1. concrete discovery question;
2. candidate type;
3. exact source schema and projected fields;
4. allowed filters/scope;
5. deterministic sort/keyset fields;
6. stream-specific hard bounds;
7. accepted serving snapshot/watermark semantics;
8. product ownership for imported candidates, exclusions, review state and conversions;
9. separate method/evaluation evidence if any ranking or scoring is introduced.

After that freeze, Data Engine #348 can implement one narrow stream and acceptance fixture proving stable page/replay behavior without a full-population scan.
