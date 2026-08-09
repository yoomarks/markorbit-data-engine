# US Docket / Alert Engine M1.0

`US_ALERT_ENGINE_M1.0` is a read-only normalization layer that turns already accepted U.S.
trademark facts and reviewed procedural evidence into poll/subscription-ready events.

It does **not** create a new legal-status source, does not reorder independent source domains, and
does not include webhook delivery or persistent subscription storage.

## API feeds

- `GET /api/us/alerts/schema`
- `GET /api/us/alerts/case-changes`
- `GET /api/us/alerts/assignments`
- `GET /api/us/alerts/ttab`
- `GET /api/us/alerts/reviewed-events`
- `GET /api/us/alerts/deadlines`

## Why cursors stay domain-scoped

US application packages, Assignment packages, and TTAB snapshots have independent source-precedence
contracts. Their `source_rank` values must never be treated as one global chronological sequence.
The Alert Engine therefore exposes a separate cursor for each source feed:

- Case changes: `(source_rank, serial_number)`
- Assignment: `(source_rank, reel_frame_id, source_package_id)`
- TTAB: `(source_rank, proceeding_number, source_package_id)`
- Reviewed USPTO events: `(source_rank, event_key)`
- Deadline candidates: serial-number bounded snapshot scan

Consumers may combine the feeds in their own UI, but must retain the event's `source_domain` and
must not reinterpret cross-domain cursor order as legal chronology.

## Stable event delivery

Every event has a deterministic `event_id`. Polling delivery semantics are:

`AT_LEAST_ONCE_POLL_CONSUMER_DEDUPE_BY_STABLE_EVENT_ID`

This makes the feeds suitable for a later notification/subscription service without making this
milestone responsible for message delivery. A corrected source observation can therefore appear on
a later poll while keeping the same logical event identity where appropriate; consumers deduplicate
by `event_id`.

## Event families

### Durable case changes

M1.4 durable observations are normalized into subscription-oriented categories such as:

- `CASE_OWNER_CHANGED`
- `CASE_OWNER_DETAILS_CHANGED`
- `CASE_STATUS_CHANGED`
- `CASE_MAINTENANCE_FACT_CHANGED`
- `CASE_PROCEEDING_FLAG_CHANGED`
- `CASE_FACT_CHANGED`

These remain observed source changes, not legal-status or ownership conclusions.

### Assignment

Only the first authoritative observation of a reel/frame becomes
`NEW_RECORDED_ASSIGNMENT`. Later source corrections to the same reel/frame remain available in the
Assignment fact layer but are not reclassified as a newly recorded assignment.

Linked serial numbers are taken only from the matching Assignment source package. Recorded assignee
names remain recordation evidence and never become a legal-title conclusion.

### TTAB

Only the first source observation of a proceeding number becomes `TTAB_NEW_PROCEEDING`. Linked
serial/registration numbers are taken from the same TTAB source snapshot. A proceeding status is a
TTABVUE procedural fact and is not converted into a win/loss or substantive-rights conclusion.

### Reviewed USPTO procedural events

The feed never guesses event-code meaning. It emits events only when the existing evidence-bound
reviewed event-role layer is `PASS`. Supported reviewed roles can normalize to events including:

- `OA_NONFINAL_ISSUED`
- `OA_FINAL_ISSUED`
- `OA_RESPONSE_OBSERVED`
- `NOA_ISSUED`
- `SOU_FILED`
- `ITU_EXTENSION_GRANTED`
- `OPPOSITION_EXTENSION_GRANTED`

If no active reviewed ruleset/evidence is ready, the feed returns no inferred events and reports the
role-layer readiness state.

### Deadline / docket candidates

Existing deadline portfolio candidates are normalized into events such as:

- `MAINTENANCE_WINDOW`
- `OA_DEADLINE_CANDIDATE`
- `NOA_SOU_DEADLINE_CANDIDATE`
- `PUBLICATION_DEADLINE_CANDIDATE`

This feed is a **snapshot candidate scan**, not an append-only source stream. Repeated polling may
return the same candidate; stable `event_id` provides deduplication. Candidate presence does not
prove the underlying legal right or deadline remains operative.

## Safety contract

Every normalized event freezes these fields:

- `actionability=REVIEW_REQUIRED`
- `legal_status_inference=false`
- `legal_ownership_conclusion=false`
- `ttab_outcome_conclusion=false`
- `substantive_rights_conclusion=false`

The schema additionally freezes:

- `global_source_rank_ordering=false`
- `source_boundary_preserved=true`
- `subscription_storage_included=false`
- `webhook_delivery_included=false`

M1.0 is therefore the event contract required before a later delivery/subscription service, not the
notification delivery system itself.
