# Foundation CN Relationship Timeline — 2026-09-18

## Verified source and failure

`markorbit_facts.cn_observed_event` is the accepted canonical CN relation history after Storage
V2. The accepted corpus contains 102,201,802 OWNER observations, 183,766 CO_OWNER observations,
154,719,376 AGENT observations, and 13,317,785 corresponding supersession observations. Each
retains application number, relation key, optional entity ID, observed/source dates, source package,
source file/lines, row hash, rank, and event hash.

The source table is ordered by `event_hash`, not application number. A read-only attempt to group
the relationship event families by application exceeded the host's 13.77 GiB ClickHouse memory
limit. The source history is therefore not an admissible serving path.

## Bounded read model and semantics

`CN_RELATIONSHIP_TIMELINE_V1` projects only OWNER, CO_OWNER, and AGENT observation/supersession
events into an application-keyed table. Runtime reads at most 5,000 events for one trademark and
fails closed above that ceiling.

The API deterministically pairs observations and supersessions by role + official relation key:

- an observation without a later supersession produces CURRENT_OWNER or CURRENT_AGENT;
- an observation with a later supersession produces FORMER_OWNER or FORMER_AGENT;
- CO_OWNER maps to the owner relationship vocabulary while retaining its source role;
- an orphan supersession fails closed instead of fabricating a source resource.

Edges use `DERIVED_FROM_OFFICIAL_HISTORY` authority and the frozen Temporal Relationship V1
contract. Source package, record hash, event hash, source file, entity ID when present, and raw
official name/address are retained. Source `event_date` is exposed only as `event_at`; `valid_from`
and `valid_to` remain null because observation/replacement dates are not asserted as legal effective
dates.

## Non-production evidence

A local fixture projection exercised one current owner and one former Agent lifecycle. `EXPLAIN
indexes = 1` selected the `application_number` primary key (1/1 local granule). Seven complete
readiness + event-read + edge-derivation runs measured p50 95.80 ms, p95 103.04 ms, and max
103.04 ms with two derived edges. This is below the frozen 400 ms indexed historical relationship
SLO on the local validation host. It is structural and latency evidence, not a production
completeness claim.

## Production boundary

The capability state is `IMPLEMENTED_REQUIRES_PRODUCTION_BACKFILL`. Runtime requires
`CN_RELATIONSHIP_TIMELINE_READY_V1` and fails closed until a separately authorized production
backfill, completeness review, READY marker, and production benchmark are complete. This change
does not mutate production ClickHouse.
