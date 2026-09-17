# ADR-FOUNDATION-001 — Temporal Relationship Contract V1

Status: Accepted for Foundation Phase 1
Issue: #715
Runtime mutation: none

## Decision

Data Engine will expose temporal relationships through the additive `MARKORBIT_TEMPORAL_RELATIONSHIP_V1` edge contract. This freezes the output semantics before serving tables, projections, or v2 routes are selected.

The contract reuses, rather than replaces:

- the existing Fact Event Envelope V1 source-fact and provenance boundary;
- the Discovery Contract V1 query hash, snapshot-bound keyset cursor, hard limits, and page provenance;
- the Integration API owner envelope and `legal_conclusion: false` rule.

The machine schema is `docs/integrations/markorbit/MARKORBIT_TEMPORAL_RELATIONSHIP_V1.schema.json`; runtime construction and fail-closed admission are implemented in `app/temporal_relationship_contract.py`.

## Resource and relationship vocabulary

V1 has a closed resource registry: TRADEMARK, ENTITY, APPLICANT, OWNER, AGENT, ATTORNEY, CORRESPONDENT, ASSIGNMENT, PROCEEDING, EVENT, GOODS, CLASS, CITATION, and REFERENCE.

V1 has a closed relationship registry covering current/former applicant, owner and agent roles; attorney/correspondent; assignor/assignee; Assignment and proceeding properties; opposition, cancellation and ex parte appeal parties; trademark goods/classes; and direct official citation/reference evidence. New jurisdiction-specific procedural roles require an additive reviewed registry change. They are not accepted as arbitrary strings.

Role edges point from the role holder to the governed resource: owner/applicant/representative to TRADEMARK, Assignment party to ASSIGNMENT, and proceeding party to PROCEEDING. `ASSIGNMENT_PROPERTY` points from ASSIGNMENT to TRADEMARK; `PROCEEDING_PROPERTY` points from PROCEEDING to TRADEMARK; trademark goods/class edges point from TRADEMARK to GOODS/CLASS. Citation edges point from the refused/citing TRADEMARK to the officially cited TRADEMARK.

## Time semantics

Every edge carries RFC 3339 UTC `observed_at`, `is_current`, and optional `valid_from`, `valid_to`, and `event_at`.

- `scope=current` selects `is_current=true`.
- `scope=historical` selects `is_current=false`.
- `scope=all` returns both under one snapshot-bound keyset cursor.
- A current edge cannot have `valid_to`.
- Unknown effective dates remain null. Observation time must not be presented as legal effective time.

## Evidence and authority

The authority levels are distinct:

- `DIRECT_OFFICIAL`: the edge is directly stated by official structured data or an official document.
- `DERIVED_FROM_OFFICIAL_HISTORY`: the edge is a deterministic, versioned derivation from retained official history.
- `INFERRED`: the edge is a Brain/Method result, never Data Engine fact truth. Its serving authority is `METHOD_OUTPUT_ONLY`.

Every edge retains official source authority/domain plus at least one stable source record, package, or document identity. Derived and inferred edges require versioned derivation identity. The fingerprint is deterministic over the complete normalized edge body.

## Citation lock

`CITED_AS_REFERENCE_FOR_REFUSAL` is admitted only when all of the following are present:

1. authority is `DIRECT_OFFICIAL`;
2. evidence kind is `OFFICIAL_DOCUMENT`;
3. Knowledge document ID and immutable version are present;
4. a precise evidence locator is present;
5. a versioned `DOCUMENT_EXTRACTION` identity is present.

A refusal/status event, status code, or generic Office Action presence is never sufficient. The later Evidence-to-Fact bridge may deliver a governed candidate, but it cannot weaken these admission rules.

## Query boundary

The v2 query surface will be a capability registry with supported resource, relationship, role, time, and jurisdiction filters. Pagination is keyset/cursor based and bound to query identity plus serving snapshot. Unsupported or unindexed filters fail closed with a capability error. Arbitrary SQL is not part of the contract.

Serving tables, sort keys, projections, and latency SLOs are intentionally deferred to #716 production-scale benchmark evidence.

## Ownership consequences

- Knowledge owns official documents, immutable versions, hashes, and evidence locators.
- Data Engine owns admitted objective relationship facts and factual history.
- Brain/Method owns inference and interpretation.
- Capability executes ACTIVE methods.
- Products/Workspaces own customer binding and workflow state.

No document corpus, semantic/legal conclusion, customer tag, CRM state, or Product workflow is introduced by this decision.
