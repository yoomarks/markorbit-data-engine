# Foundation bounded admin paging — 2026-09-18

Issue: #733  
Umbrella: #718  
Runtime mutation: none

## Verified problem

The Phase 2 audit found four admin list endpoints that executed an exact `count(*)` on every
request and used unrestricted page-number `OFFSET`. The Raw list additionally requested up to
1,000,000 filesystem records, retained them all in Python, then filtered and sliced the list.
Those paths violated the Foundation rule that operator-facing reads scale with a bounded page or
fail closed.

## Bounded behavior

The existing response remains backward compatible and adds `has_more`, `total_is_exact`, and
`total_semantics`:

- Postgres-backed package, job, and contact-task lists fetch `page_size + 1`, expose a lower-bound
  total while continuation exists, and no longer issue a separate exact-count query;
- all four endpoints cap page-number compatibility at 100 pages and 200 rows per page, so the
  largest possible offset is 19,800 rows;
- deterministic tie-breakers prevent duplicate/omitted rows inside the bounded window;
- an empty non-first page and a result continuing beyond page 100 fail closed instead of claiming
  a false total or silently hiding results;
- Raw inventory aggregation keeps only the requested bounded window in a heap instead of
  materializing the whole filesystem inventory;
- the operator Raw list stops at 100,000 eligible files with
  `RAW_INVENTORY_INDEX_REQUIRED`, requiring narrower filters or a future indexed catalog rather
  than performing a million-entry Python filter.

Exact aggregate inventory totals used outside the paged list still scan filesystem metadata, but
they retain at most the requested output limit and do not materialize the corpus. This change does
not create a second RawArtifact/document store or change source precedence.

## Boundary

This is a read-only operational repair. It adds no arbitrary SQL, schema, backfill, corpus write,
Product workflow, or production mutation. A future need to browse beyond the bounded window must
use an indexed catalog and cursor contract; raising the ceiling is not an accepted workaround.
