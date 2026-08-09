# US Monitoring Readiness M1.0

`US_MONITORING_READINESS_M1.0` answers a question that the Alert Engine event feeds alone cannot
answer safely:

> Does an empty alert page mean that no matching event was observed from a source-complete feed, or
> is the feed silent because an upstream fact/evidence domain is not ready?

The readiness layer is read-only. It does not create legal conclusions and does not make an
unaccepted source trustworthy merely because its tables are queryable.

## API

`GET /api/us/alerts/readiness`

Optional query parameters:

- `expected_history_parts` — explicit pinned count for the latest US historical application corpus.
  The system does not infer the trailing historical part count from filenames.
- `verify_sources` — when true, re-check source-file evidence through the existing acceptance layers.

## Two independent questions

Every source domain distinguishes:

- `queryable`: data can be read for investigation/monitoring output.
- `trusted`: the source/acceptance prerequisites are strong enough for downstream monitoring
  coverage to treat silence as meaningful within the scanned cursor/range.

Every alert feed then exposes:

- `queryable`
- `trusted_for_silence`
- `silence_semantics`

When `trusted_for_silence=false`, the invariant is:

`SILENCE_IS_NOT_EVIDENCE_OF_NO_EVENT`

Even when `trusted_for_silence=true`, silence means only that no matching normalized event was
observed in the requested scan cursor/range/horizon. It is not a legal conclusion that no legally
relevant event exists.

## Domains

### Application / durable history

Uses the existing strict US M1.4 source-backed acceptance audit. Historical source completeness
continues to require an explicitly pinned trailing part count. PASS is required for trusted silence;
PASS_WITH_WARNINGS or NOT_READY may remain queryable but are not trusted for silence.

### Assignment

Uses the existing Assignment readiness and acceptance layer. `SOURCE_VERIFICATION_REQUIRED` may be
queryable, but is not trusted for silence. `SOURCE_NOT_REGISTERED`, ingestion/replay blockers and
acceptance failures are not treated as monitoring-complete coverage.

Assignment recordation remains recordation evidence only and never becomes a legal ownership
conclusion.

### TTAB

Uses the existing TTAB readiness and acceptance layer. A missing/unverified TTAB source cannot be
interpreted as evidence that no opposition, cancellation, appeal or extension proceeding exists.

TTAB procedural facts remain separate from Board outcomes or substantive-rights conclusions.

### Reviewed event roles

The OA/NOA procedural-event capability is ready only when the evidence-bound reviewed event-role
layer is PASS. Unknown or unreviewed USPTO event codes are never guessed.

## Feed readiness

Five Alert Engine feeds are evaluated independently:

1. `case_changes`
2. `assignments`
3. `ttab`
4. `reviewed_events`
5. `deadlines`

The deadline feed also reports capability-level coverage:

- maintenance
- publication
- reviewed OA/NOA

This lets maintenance/publication monitoring remain visibly available when application facts are
accepted even if reviewed OA/NOA event-role evidence is not ready. In that situation the deadline
feed is `PARTIAL`, not falsely `READY`.

## Overall states

- `READY` — every feed is trusted for silence.
- `PARTIAL` — at least one feed is trusted, but monitoring coverage is incomplete elsewhere.
- `UNVERIFIED` — data is queryable, but no feed is sufficiently accepted/verified for trusted
  silence.
- `NOT_READY` — no feed is currently queryable as a monitoring source.
- `FAILED` — no feed is usable and at least one underlying domain has an integrity/runtime failure.

A failure in one independent domain does not erase accepted coverage from another domain. For
example, accepted Assignment and TTAB monitoring may remain `PARTIAL` even if the application
corpus is unavailable.

## Safety contract

The readiness response always preserves:

- `legal_status_inference=false`
- `legal_ownership_conclusion=false`
- `ttab_outcome_conclusion=false`
- `substantive_rights_conclusion=false`

The readiness layer is therefore a monitoring-coverage statement, not a trademark-rights opinion.
