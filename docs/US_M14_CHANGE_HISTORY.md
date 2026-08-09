# US M1.4 Durable Change History

US M1.4 adds a durable source-observation layer for case status and ownership changes.

## Why this exists

The `*_current` tables intentionally use `ReplacingMergeTree` and snapshot tombstones to answer
"what is current?". They are not a safe long-term audit trail because old physical versions may
eventually merge away.

`markorbit_facts.us_case_observation_history` therefore stores one append-only observation for
every USPTO case bundle published from a registered source package.

Each observation preserves raw status/location facts, key dates and flags, deterministic owner
identity/detail fingerprints, owner display names, record hashes, source package UUID, source rank,
effective date and XML member.

## Ownership semantics

Two fingerprints are deliberately separate:

- `owner_set_hash` uses normalized party name, party type, legal-entity type and nationality.
  Address-only changes do not create a false ownership transition.
- `owner_record_set_hash` covers the complete owner records. When identity is unchanged but address
  or other metadata changes, the feed reports `OWNER_DETAILS_CHANGED`.

A change in `owner_set_hash` is reported as `OWNER_IDENTITY_SET_CHANGED`. This is an observed USPTO
data change, **not** a legal conclusion that title was validly transferred.

## Status semantics

`STATUS_CODE_CHANGED` and related values are raw-source diffs only. They do not convert USPTO codes
into ACTIVE/DEAD/REGISTERED legal conclusions. Official status reference and reviewed interpretation
remain separate layers.

## Read-only APIs

- `GET /api/us/history/{serial_number}` — chronological durable observations and derived diffs.
- `GET /api/us/changes` — cursor-based global observation/change feed.
- The previously added reviewed-event deadline routes are now actually mounted on the application.

The global cursor is `(source_rank, serial_number)`. Cursor advancement follows scanned observations,
not just emitted changes, so unchanged observations cannot create an infinite loop or silent skip.

## Replay and failure safety

- retry/failure cleanup deletes observations by `source_package_id`
- clean rebuild includes `us_case_observation_history`
- deterministic source-rank replay recreates the history chain
- no change ordering depends on ingestion wall-clock time

## Production wiring fixed from PR #31

US M1.4 also closes three integration gaps left by PR #31:

1. the deadline/event-role router is mounted on FastAPI;
2. `database/postgres/init/004_us_event_roles.sql` is applied by the US schema script;
3. `validate_deadline_evidence_fixture` is executed in the existing US live CI job.

## Validation

The live `US_M1.4_DURABLE_CHANGE_HISTORY_FIXTURE` publishes two source-ranked snapshots for one
serial, changes raw status/location and owner identity, validates the timeline and global change
feed, then removes both package-scoped observations and verifies no residual history remains.
