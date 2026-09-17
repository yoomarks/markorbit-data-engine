# Foundation Read Performance Baseline — 2026-09-18

Issue: #716
Umbrella: #718
Benchmark contract: `FOUNDATION_READ_PERFORMANCE_BASELINE_V1`
Capability contract: `READ_QUERY_CAPABILITY_V1`
Runtime mutation: none

## Scope and method

This is a read-only production-scale baseline, not a storage or index migration. It measured fixed, reviewed query templates against:

- the production CN ClickHouse corpus on the existing runtime;
- the accepted US target ClickHouse corpus on port 28123.

Every benchmark used one thread, disabled query cache, returned at most 201 rows, and failed closed after 3 seconds, 1,000,000 rows, or 256 MiB. Each supported query ran seven times. Unsupported shapes ran once and were expected to hit the read budget. `EXPLAIN indexes = 1`, client elapsed time, `read_rows`, `read_bytes`, result count, `FINAL`, `OFFSET`, and exact `count(*)` use were recorded. The operator accepts named query shapes and scalar samples only; it does not accept arbitrary SQL.

Corpus sizes relevant to the result:

| Target | Table | Rows | Compressed bytes | Sort key |
| --- | --- | ---: | ---: | --- |
| CN | `cn_case_current` | 127,500,307 | 27,334,061,806 | `application_number` |
| CN | `cn_case_party_current` | 196,154,108 | 59,353,200,184 | `application_number, role, relation_key` |
| CN | `cn_applicant_name_lookup_current` | 90,265,286 | 14,806,879,668 | `normalized_name, entity_id, application_number, relation_key` |
| CN | `cn_observed_event` | 417,986,078 | 130,873,254,131 | `event_hash` |
| US | `us_case_current` | 18,074,518 | 3,539,397,320 | `serial_number` |
| US | `us_applicant_candidate_current` | 76,110,294 | 23,350,146,982 | `candidate_key, serial_number, owner_key` |
| US | `us_applicant_name_lookup_current` | 47,863,214 | 11,767,940,941 | `normalized_name, candidate_key, serial_number, owner_key` |
| US | `us_correspondent_current` | 15,505,671 | 3,973,770,462 | `serial_number, correspondent_key` |
| US | `us_event_history` | 300,970,551 | 48,982,977,427 | `event_key` |

## Supported indexed baseline

| Query | Target | p50 | p95 | Max rows read | Max bytes read | Results | SLO |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| exact application number | CN | 49.86 ms | 55.57 ms | 24,576 | 958,896 | 1 | p95 < 150 ms: pass |
| applicant/owner exact name, first page | CN | 58.55 ms | 79.57 ms | 120,103 | 17,236,415 | 51 | p95 < 300 ms: pass |
| applicant/owner exact name, cursor page | CN | 63.83 ms | 65.94 ms | 120,103 | 17,236,415 | 51 | p95 < 200 ms: pass |
| exact serial number | US | 50.64 ms | 66.95 ms | 73,569 | 4,267,083 | 1 | p95 < 150 ms: pass |
| applicant/owner exact name, first page | US | 54.40 ms | 78.57 ms | 51,342 | 9,580,103 | 51 | p95 < 300 ms: pass |
| current entity portfolio, first page | US | 62.49 ms | 84.78 ms | 91,551 | 14,010,171 | 8 | p95 < 300 ms: pass |
| current entity portfolio, cursor page | US | 60.14 ms | 64.16 ms | 91,551 | 14,010,171 | 5 | p95 < 200 ms: pass |

The plans show real primary-key pruning rather than merely fast full scans: CN exact case used 3/15,772 granules; CN owner lookup 15/11,023; US exact case 9/2,243; US owner lookup 7/5,847; and US current portfolio 12/9,764.

CN agent-name equality also completed at p95 64.75 ms, but it read all 90,960 rows and all 14 granules because the table is ordered by `agent_code`. It remains unsupported for a scale-independent API despite meeting the latency target on today's small table.

## Budget-rejected shapes

The following shapes have `PrimaryKey Condition: true` or a corpus-wide latest-state aggregation. They are frozen as capability errors until an aligned read model exists:

| Query | Target | Evidence at 1M-row budget | Required Phase 3 read path |
| --- | --- | --- | --- |
| exact registration number | US | plan 2,243/2,243 granules; 18.07M rows | registration-number lookup |
| agent/attorney name | US | plan 1,950/1,950 granules | normalized representative lookup |
| current entity portfolio | CN | plan 24,210/24,210 granules; 186.80M-row estimate | entity-first current edge projection |
| historical entity portfolio | US | all 209 Assignment assignee granules; 1.18M-row estimate before completion | normalized entity/role historical edge projection |
| filing-date range | CN / US | 127.50M / 18.07M-row estimates | date-ordered case summary projection |
| status/class list | CN / US | full case scan; US also full classification scan | status/class/date cursor projection |
| relationship timeline | CN / US | 417.99M / 300.97M-row estimates | trademark-first temporal edge/event projection |
| trademark 360 | CN / US | exact current families prune, event branch scans full history | compose only bounded family/timeline projections |
| Assignment serial lookup | US | latest-record aggregation reads 1.62M record rows | latest Assignment record projection joined after serial-keyed property lookup |
| TTAB serial lookup | US | latest-proceeding aggregation reads 1.46M proceeding rows | latest TTAB proceeding projection joined after serial-keyed property lookup |

The indexed historical relationship target remains p95 < 400 ms, but it is an acceptance target for the new read model, not a claim about the current schema.

## Existing hot-path audit

The benchmark also confirms the previously reported application-level patterns:

- `app/admin_paging_api.py` package, job, and contact-task pages execute exact `count(*)` and use `LIMIT/OFFSET`; the legacy admin API also uses `OFFSET`.
- `/api/admin/v2/raw` calls `_raw_inventory(limit=1_000_000)`, filters in Python, then slices the list.
- exact current-state reads repeatedly use `FINAL`. Indexed equality remains inside the initial SLO, but unindexed `FINAL` multiplies the cost and must not be exposed as a generic filter surface.
- Assignment and TTAB serial lookups start with indexed property facts but defeat that selectivity by rebuilding latest state across the entire parent history on every request.

No measured benchmark query used `OFFSET` or exact `count(*)`. The supported contract uses `has_more` and snapshot-bound keyset cursors.

## Frozen decision

Follow-up: the bounded two-stage repair recorded in `FOUNDATION-BOUNDED-ASSIGNMENT-TTAB-READS-2026-09-18.md` supersedes the unsupported state for Assignment and TTAB serial lookup only. The original measurements below remain the before-change evidence.

`READ_QUERY_CAPABILITY_V1.json` is the Phase 2 allowlist. Only shapes marked `SUPPORTED_INDEXED` or `SUPPORTED_INDEXED_EXACT_NAME` may be exposed as bounded generic reads. All other filters fail closed with a capability error. Low latency alone does not qualify a query when its plan scans the whole current corpus.

Phase 3 should add the smallest read models required by the rejected shapes: registration lookup; normalized representative lookup; CN entity-first current edges; current and historical temporal edges; date/status/class case summaries; trademark-first timelines; and latest Assignment/TTAB parent projections. Those structures must preserve append/history truth and must not introduce Product or Workspace semantics.
