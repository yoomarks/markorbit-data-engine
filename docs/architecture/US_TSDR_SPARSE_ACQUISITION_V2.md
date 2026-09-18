# US TSDR Sparse Acquisition V2

## Decision

USPTO Daily XML is the primary structured US trademark source. TSDR is a sparse official
verification/enrichment source, not a second US trademark mirror.

Data Engine does **not** discover commercial opportunities and does not decide that a trademark
is worth a TSDR request. Acquisition need is supplied explicitly by an upstream Capability or
product workflow.

## Resource lanes

- `STATUS_CONTACT` — current official status plus publicly exposed attorney/correspondent/owner
  contact verification when a product or Capability requests it.
- `DOCUMENT` — targeted official documents required for a live business chain or an explicitly
  selected case-research task. Historical case archives are not a default backfill.
- `LOGO` — one-shot trademark asset acquisition. A successful logo is durable and is not
  periodically refreshed. Re-acquisition requires an explicit exception/manual refresh intent.

The three lanes are independent. A serial may require one lane without the other two.

## Responsibility boundary

```text
USPTO daily XML -> Data Engine facts
                         |
                         v
           Capability / Product decision
                         |
                         v
                explicit TSDR intent
                         |
                         v
Data Engine / Knowledge / asset worker bounded execution
```

Data Engine may deduplicate, apply cooldown, enforce rate/budget limits, retry transient failures,
and preserve acquisition evidence. Those are scheduling mechanics, not opportunity discovery.

Opportunity discovery, qualification and commercial scoring remain Capability/Product concerns.

## Priority is supplied, not inferred here

Examples of upstream reasons include client/owned portfolio monitoring, an already-discovered
service opportunity, a related/cited mark, case research, and asset completion. Data Engine must
not infer those reasons from applicant nationality, missing attorney, mark quality or other facts.

## Document policy

Real-time document acquisition is narrow and business-event driven, especially OA, maintenance/
declaration and renewal chains. Historical document acquisition requires explicit case-research
selection.

## Logo storage

US logo bytes remain outside the database and use the existing content-addressed visual asset
store. Production should bind `VISUAL_RAW_PATH` to the F-drive asset root. Data Engine retains
asset identity/hash/metadata rather than database blobs.
