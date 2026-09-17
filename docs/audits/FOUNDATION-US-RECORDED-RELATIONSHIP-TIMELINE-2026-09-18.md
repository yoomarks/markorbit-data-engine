# Foundation US Recorded Relationship Timeline Audit — 2026-09-18

## Outcome

The authenticated Integration V1 route
`GET /api/v1/us/cases/{serial_number}/relationships?scope=current|historical|all`
now maps bounded USPTO Assignment and TTAB facts into
`MARKORBIT_TEMPORAL_RELATIONSHIP_V1` edges.

No new serving table was required. Both source property tables already lead with the USPTO
serial number, and the accepted two-stage reads already cap resolution at 500 Assignment
records or TTAB proceedings. The generic read reuses those candidate reads, verifies the
selected source package, and caps the combined response at 5,000 edges.

## Corpus evidence

Local accepted-corpus observations:

| Fact table | Rows | Distinct linked identity |
| --- | ---: | ---: |
| `us_assignment_property_history` | 7,938,877 | 2,654,649 serials |
| `us_assignment_assignor_history` | 1,773,794 | 1,592,267 reel/frame records |
| `us_assignment_assignee_history` | 1,700,698 | 1,592,267 reel/frame records |
| `us_ttab_property_history` | 2,261,721 | 771,996 serials |
| `us_ttab_party_history` | 1,487,818 | 663,077 proceedings |

`EXPLAIN indexes = 1` for serial `74720819` selected the TTAB property primary key on
`serial_number`, reading 6 of 289 granules. Seven complete warm relationship reads returned
13 edges with p50 377.82 ms and a maximum of 396.36 ms, within the 400 ms candidate SLO for
indexed historical relationship reads.

## Relationship and authority semantics

The route emits direct official historical edges for:

- trademark → Assignment as `ASSIGNMENT_PROPERTY`;
- official Assignment party → Assignment as `ASSIGNOR` or `ASSIGNEE`;
- trademark → TTAB proceeding as `PROCEEDING_PROPERTY`;
- raw TTAB XML parties whose source explicitly names the side as Plaintiff/Defendant or
  Applicant → the frozen OPP/CAN/EXA role vocabulary.

All edges are `DIRECT_OFFICIAL` with `DIRECT_MAPPING` provenance and retain observation key,
package, source file, record hash, observation timestamp, and the applicable recorded or
filing date. They are historical recorded/procedural facts (`is_current=false`), not legal
title, proceeding outcome, or substantive-rights conclusions. Consequently `scope=current`
returns no inferred relationship.

Current official TTAB bulk rows use retained `P`/`D` role codes represented as `ROLE_P` and
`ROLE_D`. The parser intentionally does not infer plaintiff/defendant semantics from those
codes. The generic route preserves that boundary: it still emits the directly linked
`PROCEEDING_PROPERTY` edge and reports skipped party facts in `unmapped_ttab_party_count`.
It does not fabricate a procedural role.

## Verification

- focused relationship, Assignment, TTAB, integration-contract, temporal-contract, and
  capability-registry tests pass;
- Ruff passes on all changed Python surfaces;
- runtime reads were executed only against the local development ClickHouse;
- no production data, readiness marker, or schema state was mutated.
