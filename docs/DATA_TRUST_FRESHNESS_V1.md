# MarkOrbit Data Trust / Freshness V1

`MARKORBIT_DATA_TRUST_FRESHNESS_V1`

Data Trust V1 answers a question that raw query results cannot answer safely:

> If Data Engine did not return a source observation, is that silence actually trustworthy?

The answer is decomposed into five independent dimensions:

```text
Queryable
Complete
Fresh
Accepted
Trusted for silence
```

## Trusted-for-silence semantics

`trusted_for_silence=true` means only:

```text
NO_SOURCE_OBSERVATION_WITHIN_VERIFIED_COVERAGE_NOT_LEGAL_NONEXISTENCE
```

It does **not** mean a legal event, right, obligation, deadline, proceeding, owner, filing, or other fact does not exist in law or outside the verified source coverage. It cannot authorize business action.

Trusted silence requires all of the following:

- query plane ready;
- source identity complete;
- registered corpus complete;
- source verification passed;
- explicit source coverage meets the domain-required coverage boundary;
- domain acceptance passed;
- the source/domain explicitly supports absence inference.

If any gate is missing or false, silence is untrusted and a reason code explains why.

## Freshness

The generic engine does not guess whether a source is daily, weekly, monthly, irregular, or event-driven.

A domain supplies:

```text
coverage_through
required_coverage_through
```

Data is fresh only when the actual verified boundary meets or exceeds the required boundary. This keeps source cadence and holiday/publication peculiarities in the jurisdiction adapter rather than embedding global assumptions in the platform.

## Completeness

Completeness requires three distinct facts:

- source identity is complete;
- the registered corpus expected by the domain is complete;
- source verification has passed.

A package/job finishing without error is not completeness and is not acceptance.

## Acceptance

V1 recognizes formal accepted/pass states only. Existing domain acceptance reports remain authoritative and unchanged; this contract consumes their status rather than replacing their domain-specific audits.

## Intended consumer use

MO Brain or another consumer may use the trust result to distinguish:

```text
no observed event + trusted_for_silence=true
```

from:

```text
no observed event + stale/incomplete/unaccepted data
```

This is a source-observation confidence boundary, not a legal conclusion.
