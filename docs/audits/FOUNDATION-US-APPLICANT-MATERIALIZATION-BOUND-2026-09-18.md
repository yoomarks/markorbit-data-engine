# Foundation US applicant materialization bound — 2026-09-18

Issue: #735  
Umbrella: #718  
Runtime mutation: none

## Verified problem

US applicant candidate revalidation, name discovery, and portfolio reads preserve a deterministic
source fingerprint over the current official owner contributions. The existing reader allowed a
single request to return up to 1,000,001 contribution rows before rejecting the candidate. The
query had an overall ClickHouse read budget, but Python materialization could still approach one
million rows and therefore failed the Foundation acceptance boundary.

## Decision

`MAX_APPLICANT_CONTRIBUTION_ROWS` is 10,000. Both exact-candidate and multi-candidate
materialization queries request at most 10,001 rows and reject the result when the sentinel row is
present. Admitted candidates keep the existing epoch, exact-source fingerprint, cursor, current
fact, and provenance semantics. Oversized candidates fail closed through the existing
`OwnerReadUnavailable` boundary; the runtime does not truncate contributions or publish a partial
fingerprint.

The separate one-million-row ClickHouse `max_rows_to_read` setting remains a database scan budget,
not a Python result/materialization allowance. Lowering that scan budget without production query
plan evidence could reject otherwise indexed reads and is outside this repair.

## Boundary

This change is read-only. It does not alter a table, projection, serving epoch, cursor shape,
source fingerprint algorithm, Product workflow, or production corpus. Supporting a candidate with
more than 10,000 current contributions requires a reviewed aggregate/fingerprint serving model;
raising the Python ceiling is not an accepted workaround.
