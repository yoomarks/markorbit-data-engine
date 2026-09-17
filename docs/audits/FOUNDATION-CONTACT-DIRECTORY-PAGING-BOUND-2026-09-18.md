# Foundation contact directory paging bound — 2026-09-18

Issue: #737  
Umbrella: #718  
Runtime mutation: none

## Verified problem

The primary `/api/admin/contacts/directory` runtime selected candidates from contact-owned tables
before hydrating trademark evidence, but still used unrestricted `OFFSET` and `count(*) OVER()` on
every page. It was the remaining primary operator list whose work and exact-total calculation could
grow with the complete filtered contact directory.

## Bounded behavior

- The API and cache boundary reject offsets above 20,000.
- The runtime fetches `limit + 1`, hydrates channels and trademark evidence only for the returned
  page, and uses the sentinel row to expose `has_more`.
- Existing `total`, `limit`, `offset`, and `rows` fields remain. While continuation exists, `total`
  is an explicit lower bound; additive `total_is_exact` and `total_semantics` fields prevent an
  exact-count claim.
- Ordering remains deterministic by country, normalized entity name, and entity ID.
- An empty non-first page reports an upper-bound total rather than inventing an exact count.

The legacy internal compatibility helper in `directory_api.py` is not the routed/cached directory
list implementation and is unchanged. Any future caller must use the primary bounded runtime or
receive its own reviewed migration; it is not evidence for a Product-facing arbitrary query.

## Boundary

This is a read-only operator API repair. It does not change contact ownership, inference,
analytics, schema, cache generation, Product workflow, or production data. If browsing beyond the
bounded window becomes necessary, the next step is a snapshot-bound entity-ID cursor, not a larger
OFFSET ceiling.
