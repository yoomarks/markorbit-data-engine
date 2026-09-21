# Seed CN/US data readiness — 2026-09-21

## Decision

The first CN/US Agency Seed cohort is data-ready at the Data Engine read plane once #796 is merged and read-only production-smoked.

The required V1 traversal is source-native and bounded:

- CN: agent exact-name/current agent -> agent entity portfolio -> selected application -> current applicant/owner candidates -> exact applicant review -> bounded portfolio.
- US: attorney/current or TTAB correspondent history -> selected serial -> current applicant candidates -> exact applicant review -> bounded portfolio / recorded Assignment+TTAB history.

The case-to-applicant bridge deliberately prevents Lite from taking an owner display name from a trademark and re-running name search as if that reconstructed identity context.

## Required vs optional

Corporate-registry and marketplace facts are optional strengtheners under MarkOrbit #1396, not blockers for the first Agency Seed journey. No authorized/frozen adapter is currently available in this Data Engine for those sources, so the matrix marks them `NOT_COVERED_NO_AUTHORIZED_SOURCE`.

CN Assignment/transfer evidence is also not claimed: no authorized source-native CN transfer adapter is frozen in this lane. US recorded Assignment facts are available as bounded review facts, but they do not establish legal ownership.

## Permanent boundaries

- Source candidate != verified legal identity.
- Agent/attorney/correspondent history != Customer Relationship or current appointment.
- Recorded assignment != legal title conclusion.
- Corporate status, when eventually added, != legal succession conclusion.
- Marketplace listing, when eventually added, != ownership or valuation.
- Data Engine returns source facts, provenance, currentness and bounded candidates; Brain owns cross-source identity/inference and Product/Workspace owns local business truth.

Machine-readable matrix: `docs/integrations/markorbit/SEED_DATA_READINESS_V1.json`.
