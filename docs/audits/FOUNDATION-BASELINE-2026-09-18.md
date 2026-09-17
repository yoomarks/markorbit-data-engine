# Foundation Baseline — 2026-09-18

Status: read-only Phase 0 baseline for #718. No production corpus or storage mutation was performed.

## Repository and coordination state

| Repository | Audited `origin/main` | Open work relevant to this lane |
| --- | --- | --- |
| `markorbit-data-engine` | `084a9f8e08b2e643e1a537d1e8252081b8b70d5c` | #714–#718 open; no open PR |
| `markorbit-knowledge` | `8ef19d39d4e40551a74dc3f966a46bbfd6699c12` | Draft PR #781 preserves the CNIPA auth/evidence boundary and keeps judgment routes/schema unverified |
| `markorbit` | `4c0c680f92300fd56634d1ecfa84848395eb0505` | no open PR |

The locked ownership boundary is consistent across the three repositories: Knowledge owns documents and immutable evidence; Data Engine owns objective structured facts and factual history; Brain owns validated interpretation/research methods; Capability executes ACTIVE methods; Products own business lifecycle state.

## Existing Data Engine foundation

- Integration API v1 already provides exact CN/US case reads, US case 360/history, Assignment, TTAB, change feed, US applicant name discovery, and CN/US applicant portfolio reads.
- Applicant/portfolio transport already has query-hash-bound cursors, serving-epoch snapshot binding, `has_more`, stable source references, content fingerprints, and request provenance. It is a reusable base for v2.
- Existing resources are still applicant/owner-specific. Agent, attorney, correspondent, Assignment party, and TTAB party are not generic first-class entity resources, and the portfolio contract is current-state oriented.
- Assignment and TTAB retain official history, but their current read APIs are serial/proceeding focused. They do not provide indexed generic entity timelines.
- CN retains current cases/parties/agents, objective observed events, case relations, and current applicant-name lookup. Storage V2 intentionally makes `cn_observed_event` the canonical OWNER/CO_OWNER/AGENT relationship history; the legacy wide party-history table is empty by design.
- US retains current case/owner/correspondent/classification families, case observations, event history, Assignment history, and TTAB proceeding/party/property/docket history.
- No canonical direct trademark citation/reference edge exists. Refusal/status events alone cannot create `CITED_AS_REFERENCE_FOR_REFUSAL`.

## Read-only production-scale inventory

Observed from `system.tables` on 2026-09-18. These are active-part metadata counts, not new corpus acceptance or mutation.

### CN serving runtime

| Table | Rows | Compressed size | Current sort key |
| --- | ---: | ---: | --- |
| `cn_case_current` | 127,500,307 | 25.46 GiB | `application_number` |
| `cn_case_party_current` | 196,154,108 | 55.28 GiB | `application_number, role, relation_key` |
| `cn_applicant_name_lookup_current` | 90,265,286 | 13.79 GiB | `normalized_name, entity_id, application_number, relation_key` |
| `cn_agent_current` | 90,960 | 12.61 MiB | `agent_code` |
| `cn_observed_event` | 417,986,078 | 121.89 GiB | `event_hash` |
| `cn_case_relation_current` | 481,639 | 92.85 MiB | source/target application + relation type |
| `cn_case_party_relation_history` | 0 | 0 B | `history_hash` |

### Accepted US target

| Table | Rows | Compressed size | Current sort key |
| --- | ---: | ---: | --- |
| `us_case_current` | 18,074,518 | 3.30 GiB | `serial_number` |
| `us_owner_current` | 40,133,031 | 8.86 GiB | `serial_number, owner_key` |
| `us_applicant_candidate_current` | 76,110,294 | 21.75 GiB | `candidate_key, serial_number, owner_key` |
| `us_applicant_name_lookup_current` | 47,863,214 | 10.96 GiB | `normalized_name, candidate_key, serial_number, owner_key` |
| `us_correspondent_current` | 15,505,671 | 3.70 GiB | `serial_number, correspondent_key` |
| `us_event_history` | 300,970,551 | 45.62 GiB | `event_key` |
| `us_case_observation_history` | 22,829,018 | 7.05 GiB | `serial_number, source_rank, source_package_id` |
| Assignment history (4 tables) | 13,032,073 | about 2.57 GiB | record/party by reel-frame; property by serial |
| TTAB history (4 tables) | 11,264,134 | about 1.75 GiB | proceeding keyed; property additionally serial keyed |

## Confirmed read-path constraints

- Exact case reads align with primary sort keys, but many current-state reads use `FINAL`; US case 360 issues repeated `FINAL` queries across multiple large fact families.
- Existing applicant-name lookup projections align with normalized-name discovery, but exact applicant materialization can read up to 1,000,001 rows and then assemble results in Python.
- CN party current is ordered by application, not entity; US owner/correspondent are ordered by serial. Generic entity portfolio/history queries therefore require dedicated serving structures rather than scans of source tables.
- Assignment party history is ordered by reel-frame and TTAB party history by proceeding. Name-based party resolution is not indexed.
- `cn_observed_event` is ordered by hash, so role/time relationship timelines are not a bounded serving query on the source history table.
- Admin package/task list endpoints still use page-number `LIMIT/OFFSET` and exact `count(*)` per request.
- Admin raw inventory paging still materializes up to 1,000,000 filesystem entries and filters/pages them in Python.
- The v1 machine contract has a one-million-row read budget, but there is no v2 capability registry that rejects unsupported filters before a large scan.

## Knowledge foundation already present

- Schema v1 already owns strict SourceDefinition and immutable RawArtifact identity, hashes, storage URI, source provenance, and supersession chains.
- Conversion/staging, canonical-document verification, Current Governed Knowledge, ReadyPackage v1/v2, and delivery evidence already exist and should be extended rather than replaced.
- USPTO TSDR is already a foundational `STATUS_AND_DOCUMENTS` coverage target with HTML/Markdown/JSON/PDF/image artifact expectations, but there is no frozen trademark case-document taxonomy or value/download policy for OA → Response → Next Action → Outcome chains.
- CNIPA source coverage and an authenticated evidence-only judgment acquisition seam already exist for registration examination, opposition decision, and review adjudication. The response schema/identity/coverage remains explicitly unverified pending permitted live evidence; Draft PR #781 further freezes that fail-closed boundary.
- CNIPA currently lacks the complete requested high-value taxonomy/value policy for refusal, review, opposition, invalidation, and non-use decisions.
- Knowledge does not own semantic/legal extraction. A cross-repository Fact Candidate contract with document/version/locator plus method provenance does not yet exist.

## Phase 1 implications

Phase 1 should freeze one additive, jurisdiction-neutral temporal relationship contract before adding tables. It should reuse the v1 owner envelope, cursor/snapshot/provenance semantics, preserve source-specific fact authority, and make unsupported relationship/filter capabilities fail closed. Serving structures and indexes should follow the Phase 2 benchmark/query-capability evidence rather than precede it.

The first contract must distinguish `DIRECT_OFFICIAL`, `DERIVED_FROM_OFFICIAL_HISTORY`, and `INFERRED`; retain current and historical edges; and prohibit citation edges without direct auditable document evidence.
