# US Trademark Case 360 M1.0

`US_CASE_360_M1.0` is a read-only composite view over already accepted U.S. trademark fact domains.
It does not create a new legal-status table and does not collapse independent USPTO/TTAB/derived
sources into one asserted conclusion.

## API

- `GET /api/us/case-360/schema`
- `GET /api/us/cases/{serial_number}/360`

Optional query parameters on the case endpoint:

- `as_of`
- `history_limit` (1-5000)
- `assignment_limit` (1-500)
- `ttab_limit` (1-500)

## Source domains

The view composes six independently labeled domains:

1. **Application facts** — current USPTO case, owner, classification, event, statement,
   correspondent, design-search, prior-registration, foreign-application and Madrid facts.
2. **Durable change history** — append-only M1.4 case observations plus derived diffs.
3. **Recorded assignments** — latest recorded Assignment observations and exact normalized
   owner/assignee name-set comparison.
4. **TTAB procedural facts** — M1.1 proceedings linked by serial number.
5. **Reviewed deadline evidence** — existing reviewed event-role evidence layer.
6. **Maintenance metadata** — existing evidence-backed post-registration deadline calculator.

Each domain retains its own `semantics` marker. A failure in an auxiliary domain is returned as
`NOT_AVAILABLE` for that domain and does not erase otherwise usable case facts.

## Safety contract

The Case 360 response always preserves these boundaries:

- `source_boundary_preserved=true`
- `legal_status_inference=false`
- `legal_ownership_conclusion=false`
- `ttab_outcome_conclusion=false`
- `substantive_rights_conclusion=false`

A recorded Assignment name match is not legal-title proof. A TTAB status or due-date observation is
not a Board outcome or automatically actionable deadline. A maintenance schedule is deadline
metadata and does not prove that a registration remains legally valid.

## M1.0 scope

M1.0 is intentionally a composition milestone. It adds no new source ingestion and no new mutable
fact tables. Future Case 360 milestones may add richer cross-domain evidence reconciliation only
when the underlying fact domains have their own accepted source contracts.
