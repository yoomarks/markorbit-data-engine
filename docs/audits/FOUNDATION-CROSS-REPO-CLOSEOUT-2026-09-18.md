# MarkOrbit Foundation cross-repository closeout — 2026-09-18

Status: **Foundation accepted with fail-closed production activation gates**  
Umbrella: #718  
Repositories: `markorbit-data-engine`, `markorbit-knowledge`, `markorbit`  
Production mutation performed by this lane: **none**

## Acceptance decision

The Foundation architecture and reusable contracts are accepted. Knowledge remains the document
owner; Data Engine remains the objective fact/history owner; Brain owns researched methods;
Capability remains the executor for ACTIVE methods; Products retain customer and workflow state.

Acceptance means the contracts, bounded read paths, non-production serving structures, evidence
seams, fixtures, and operational guardrails are ready for governed consumers. It does **not** mean
that every new projection is backfilled in production, that real citation facts exist, or that a
risk method is ACTIVE. Those boundaries are intentionally fail closed.

## Cross-repository delivery ledger

### Data Engine

| Phase | PR | Merge commit | Outcome |
| --- | --- | --- | --- |
| 0 | #719 | `354ffcf899a6f903967d7a19e7998c716562cc51` | Fresh CN/US/API/Knowledge baseline |
| 1 | #720 | `9006ecebb607faf83188080052849d80f028126b` | Temporal relationship contract V1 |
| 2 | #721 | `161e8d8bca51b190536653f850b10389e9d8d518` | Production-scale read benchmark and capability allowlist |
| 3 | #722 | `e60b1b94668fa92116e648b50297ad23fd60f9bf` | Capability contracts exposed through existing authenticated contract route |
| 3 | #723 | `e59ac79aa85ba518be26b3a35fc5a764a3cb4498` | Bounded two-stage Assignment/TTAB reads |
| 3 | #724 | `e427a85d88ac8a544ed2c25bd10476c3bf0b8ad7` | US registration-number serving model and route |
| 3 | #725 | `8012c0d0190187332e4dd2a8028219842aae3132` | Trademark 360 reuses bounded Assignment read |
| 3 | #726 | `b3cb1ca7e41e26cec8245d4674ceeab6a319455f` | US serial-keyed event timeline model and route |
| 3 | #727 | `49cd56f80eeba85a9de50a607a2bf20d8b1d2bc1` | US attorney exact-name candidate lookup |
| 3 | #728 | `e6f0749ce2a788f251849c0d76272c7543df0265` | CN Agent exact-name candidate lookup |
| 3 | #729 | `8671e69fb462e569553b9ae4fff10c2440a8f4c0` | CN current/historical/all relationship timeline |
| 3 | #730 | `fa41ff08cbae8eff294c1ae7f5b4e81a5e0a5c5a` | US recorded Assignment/TTAB relationship timeline |
| 7 | #732 | `9f858a39acb60abb31054c579ee1c890e337b86f` | Storage Topology V2 guardrails |
| 9 | #734 | `f123f018d240620e639670c2e335651778397140` | Bounded admin paging and Raw inventory memory/scan budget |
| 9 | #736 | `48d3295e45da551516d521e668cbd31a58b28daf` | US applicant contribution materialization bound |
| 9 | #738 | `377285ae3cf56915103d8c40b2d72ff6e0e802c6` | Bounded primary contact-directory paging |

### Knowledge

| Phase | PR | Merge commit | Outcome |
| --- | --- | --- | --- |
| 4 | #784 | `5f98e0c0666b80b81a374108c8a2f44db1d099cb` | Targeted USPTO TSDR index/binary acquisition policy |
| 4 | #786 | `d98ddcbbe0426d7d95711b7e1405fb551e4e395c` | Deterministic document-family classifier |
| 5 | #788 | `a5f840f14050ed1936320ff17c700228b9d17877` | ADR-0013 structured-source versus official-document routing |

### MarkOrbit main

| Phase | PR | Merge commit | Outcome |
| --- | --- | --- | --- |
| 6 | #1329 | `10606e02d98b00f9db4a58eee8c8eee07c75aa86` | Evidence-to-Fact Candidate V1 plus CN/US fixtures |
| 8 | #1331 | `ccb481c3e96c356c58ec123ddadbeb14940b2703` | Seven Brain-owned research method-family contracts |

## Accepted contract and model versions

- `MARKORBIT_DATA_ENGINE_INTEGRATION_V1`: stable authenticated transport; storage-independent,
  additive compatibility.
- `MARKORBIT_TEMPORAL_RELATIONSHIP_V1`: closed resource/relationship vocabulary,
  current/historical/all scope, deterministic identity, evidence and authority separation.
- `READ_QUERY_CAPABILITY_V1`: named allowlist, snapshot-bound keyset cursors, no arbitrary SQL,
  3-second / 1,000,000-row / 256-MiB default scan budget, fail-closed unsupported filters.
- `FOUNDATION_READ_PERFORMANCE_BASELINE_V1`: reproducible seven-run production-scale benchmark.
- Serving/readiness models: `US_REGISTRATION_CANDIDATE_LOOKUP_SCHEMA_V1`,
  `US_EVENT_SERIAL_LOOKUP_SCHEMA_V1`, `US_ATTORNEY_NAME_LOOKUP_SCHEMA_V1`,
  `CN_AGENT_NAME_LOOKUP_SCHEMA_V1`, and `CN_RELATIONSHIP_TIMELINE_SCHEMA_V1`.
- `DATA_ENGINE_STORAGE_TOPOLOGY_V2`: D/E/F roles, physical/VHDX reserves, evidence-based E
  allocation, promotion review, read-only audit and alerts.
- Knowledge TSDR acquisition policy revision `2026-09-18` and classifier
  `uspto-tsdr-document-family@1.0.0`.
- Knowledge `ADR-0013`: native structured facts route to Data Engine; official document bytes and
  immutable versions route to Knowledge.
- `MARKORBIT_FACT_CANDIDATE_V1`: first admitted candidate vocabulary is only
  `CITED_AS_REFERENCE_FOR_REFUSAL`.
- `MARKORBIT_TRADEMARK_INTELLIGENCE_METHOD_FAMILY_V1`: seven Brain-owned, `RESEARCH_ONLY`,
  activation-ineligible method families.

## Verified corpus and performance evidence

The read-only Phase 0/2 inventory observed the production CN corpus and accepted US target:

| Corpus/table | Rows |
| --- | ---: |
| CN case current | 127,500,307 |
| CN party current | 196,154,108 |
| CN applicant-name lookup | 90,265,286 |
| CN observed events | 417,986,078 |
| US case current | 18,074,518 |
| US owner current | 40,133,031 |
| US applicant candidates | 76,110,294 |
| US applicant-name lookup | 47,863,214 |
| US correspondent current | 15,505,671 |
| US event history | 300,970,551 |
| US Assignment history | 13,032,073 |
| US TTAB history | 11,264,134 |

Production-scale seven-run results for already accepted indexed reads:

| Query | p50 | p95 | Result |
| --- | ---: | ---: | --- |
| CN exact application | 49.86 ms | 55.57 ms | pass `<150 ms` |
| CN owner exact name, first page | 58.55 ms | 79.57 ms | pass `<300 ms` |
| CN owner exact name, cursor page | 63.83 ms | 65.94 ms | pass `<200 ms` |
| US exact serial | 50.64 ms | 66.95 ms | pass `<150 ms` |
| US owner exact name, first page | 54.40 ms | 78.57 ms | pass `<300 ms` |
| US current portfolio, first page | 62.49 ms | 84.78 ms | pass `<300 ms` |
| US current portfolio, cursor page | 60.14 ms | 64.16 ms | pass `<200 ms` |

Primary-key evidence included CN exact case 3/15,772 granules, CN owner lookup 15/11,023,
US exact case 9/2,243, US owner lookup 7/5,847, and US portfolio 12/9,764.

Additional bounded evidence:

- Assignment/TTAB two-stage serial reads: accepted target seven-run p95 about 50–60 ms.
- US recorded relationship timeline: accepted corpus p50 377.82 ms, maximum 396.36 ms; TTAB
  property plan selected 6/289 serial-key granules.
- Local non-production projection checks: US registration p95 13.324 ms; US event timeline p95
  6.879 ms; US attorney name p95 145.61 ms; CN Agent name p95 155.53 ms; CN relationship
  timeline p95 103.04 ms. These are structural/latency evidence, not production activation claims.

## Phase 9 acceptance matrix

| Requirement | Result | Evidence/qualification |
| --- | --- | --- |
| Knowledge owns documents | PASS | TSDR policy reuses immutable RawArtifact/version lineage; ADR-0013 forbids Knowledge fact/legal admission. |
| Data Engine owns facts, not document corpus | PASS | Temporal edges retain document references only; no PDF/document store or arbitrary document read was added. |
| No arbitrary SQL | PASS | `READ_QUERY_CAPABILITY_V1` is a closed named registry; unsupported/unindexed filters return capability errors. |
| Current/history relationships queryable | PASS, gated | CN OWNER/CO_OWNER/AGENT exposes current/historical/all via an application-keyed model; US exposes bounded recorded Assignment/TTAB history. New CN projection remains unavailable until governed production backfill/READY. |
| Refusal event cannot fabricate citation edge | PASS | Temporal contract requires direct official document/version/locator and versioned extraction identity; ordinary status/refusal events are insufficient. |
| Provenance is traceable | PASS | Temporal edges retain package/record/event provenance; Fact Candidate retains Knowledge document/version/locator, method/version and reproducible fingerprint. |
| Real production-scale benchmark exists | PASS | Phase 2 benchmark used production CN and accepted US target with plans, p50/p95, rows/bytes, results, FINAL/OFFSET/count audit and fixed budgets. |
| No primary deep OFFSET dependency | PASS | Integration reads use keyset cursors; primary admin and contact lists now have bounded compatibility offsets plus lookahead/fail-closed overflow. |
| No million-row Python filtering/materialization in primary APIs | PASS | Raw list has bounded retention and a 100,000-file scan gate; US applicant contribution materialization is capped at 10,000; oversized reads fail closed. |
| Relationship query has aligned index/model | PASS, gated | CN application-keyed relationship projection and US serial-keyed Assignment/TTAB sources have plan evidence; CN production use waits for backfill/READY. |
| CN/US citation proof fixtures | PASS as contract proof | `MARKORBIT_FACT_CANDIDATE_V1` has deterministic CN/US synthetic fixtures and rejection tests; there is no claim of real admitted citation facts. |
| Storage topology not mutated without authority | PASS | V2 is config/audit/alert/documentation only; no MOVE/ALTER/TTL/OPTIMIZE/VHDX/source mutation ran. |
| No Product interaction introduced | PASS | No Lite/Workspace/CRM/customer workflow, notification, task or UI was added. |

## Current limitations and fail-closed states

1. US registration, US event timeline, US attorney lookup, CN Agent lookup, and CN relationship
   timeline are `IMPLEMENTED_REQUIRES_PRODUCTION_BACKFILL`. Their runtime READY markers are absent by
   design until separately authorized backfill, completeness review, and production benchmark.
2. There is no admitted real CN or US citation-edge population. The Evidence-to-Fact bridge is a
   shared contract/fixture path only; Data Engine citation persistence/admission and a real ACTIVE
   extraction execution remain future work.
3. TSDR V1 is targeted policy/classification only. It does not register an API key, perform live
   HTTP acquisition, download/convert documents, or publish a production ReadyPackage.
4. CNIPA authenticated document acquisition remains fail closed where live schema/session evidence
   is unavailable. No CAPTCHA/SSO bypass or unverified completeness claim is accepted.
5. All seven intelligence method families remain `RESEARCH_ONLY`. There is no defensive-risk
   engine, legal conclusion, Capability execution, alert, CRM task, or Product workflow.
6. Unsupported date/status/class/generic historical filters remain explicit capability errors
   until a measured serving model exists. The Foundation does not expose an arbitrary fallback
   scan.
7. The internal legacy `contact_ingest/directory_api.py` list helper still contains OFFSET/count
   compatibility SQL but is not the routed/cached primary directory implementation. It is not a
   supported Foundation query surface.

## Production-only operations not executed

- ClickHouse migration apply, projection backfill, production READY marker creation, or bulk replay;
- production completeness acceptance for the new registration/event/name/relationship models;
- live storage `MOVE`, `ALTER`, `TTL`, `OPTIMIZE`, storage-policy mutation, VHDX relocation, or
  source deletion;
- live USPTO TSDR/CNIPA document acquisition or production ReadyPackage publication;
- real citation Fact Candidate extraction, validation, Data Engine admission, or population build;
- activation/execution of any Brain method family or defensive-risk computation.

Each item requires its own evidence, authority, rollback/acceptance plan, and—where it changes the
production corpus or storage—the explicit production authorization defined by #718.

## Capabilities available to the next Product phase

Products and external consumers may use the authenticated, versioned contracts without knowing
ClickHouse table layout:

- exact CN application and US serial lookup;
- exact applicant/owner discovery and current portfolio with snapshot-bound cursors;
- bounded Assignment and TTAB lookup plus recorded/procedural US relationship timeline;
- capability/schema discovery and fail-closed unsupported-query behavior;
- change feed/delta and existing provenance envelopes;
- non-production-ready contracts for exact US registration, US event timeline, US attorney name,
  CN Agent name, and CN current/historical relationship timelines;
- Knowledge targeted high-value TSDR acquisition admission and deterministic classification;
- Evidence-to-Fact Candidate V1 for a future governed citation extraction/admission implementation;
- research-only trademark intelligence method-family descriptors;
- read-only storage topology placement/capacity audit and alert evaluation.

Product work must continue to store customer binding, confirmation, labels, opportunities, tasks,
notifications, and workflow state in Product/Workspace owners. It must not reinterpret a Candidate,
Provider Return, payment, or research output as Official Truth.
