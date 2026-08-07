# CN Case Status Inference Model V1

Status: DESIGN FREEZE CANDIDATE

This document defines a separate inference layer for Chinese trademark case status/outcome reasoning. It must remain distinct from official source facts and from item-level goods status codes.

## 1. Core principle

The system has four separate layers:

1. **Official observed facts** — filing date, preliminary publication date, registration publication date, validity dates, goods item status codes, etc.
2. **Goods-scope state** — which goods remain operationally effective / inactive after reconstructing the complete goods set.
3. **Case-status inference** — probable procedural/legal outcome inferred from timing + publication milestones + reconstructed goods changes.
4. **Cause attribution** — probable cause such as withdrawal, refusal, opposition loss, invalidation, non-use cancellation, cancellation, or non-renewal.

Layers 3 and 4 are inference only unless separately confirmed by an official event/notice.

## 2. Evidence dimensions

The inference engine should use at least:

- filing_date
- prelim_pub_date
- registration_pub_date
- valid_from / valid_until
- elapsed days/months from filing
- elapsed years from registration
- whether preliminary publication exists
- whether registration publication exists
- complete reconstructed goods set
- count / ratio of goods with code 1
- count / ratio of goods with code 2
- first observation date of goods loss
- whether loss is partial or total
- source package effective date / source rank
- later official notices/events when available

Goods code alone never determines the case-level cause.

## 3. Candidate inference rules

### R1 — Very early total loss after filing

**Pattern**

- within roughly 3 months after filing;
- no meaningful examination/publication milestone yet;
- all known goods become code `2`.

**Candidate inference**

- `LIKELY_VOLUNTARY_WITHDRAWAL`

**Reasoning**

A total loss before substantive examination is materially more consistent with applicant withdrawal than with a completed refusal, opposition, invalidation, cancellation, or non-renewal process.

**Confidence**

- HIGH when all goods change together and no preliminary publication exists;
- lower if an examination/refusal event is separately observed.

### R2 — Preliminary publication followed by partial goods loss

**Pattern**

- preliminary publication exists;
- only part of the complete goods set becomes code `2`;
- some goods remain operationally effective.

**Candidate inference**

- `LIKELY_PARTIAL_REFUSAL_OR_PARTIAL_ADVERSE_DECISION`

**Reasoning**

A partial goods loss around the examination/publication stage is consistent with a partial refusal or another partial adverse examination outcome.

**Important limitation**

Do not force the cause to `PARTIAL_REFUSAL` without additional evidence. Opposition or another post-publication event can also create partial loss.

### R3 — Preliminary publication, no registration publication, eventual total loss

**Pattern**

- preliminary publication exists;
- registration publication does not appear after a materially long interval;
- all known goods eventually become code `2`.

**Candidate inference**

- `LIKELY_OPPOSITION_LOSS_OR_OTHER_POST_PUBLICATION_TOTAL_ADVERSE_OUTCOME`

**Reasoning**

The case passed examination into preliminary publication but never completed registration, and all goods were later lost. Opposition loss is a strong candidate, but should not be asserted as certain without an opposition event/notice.

### R4 — Preliminary publication, registration publication exists, partial goods loss

**Pattern**

- preliminary publication exists;
- registration publication exists;
- only part of the complete goods set becomes code `2` around/after the publication process.

**Candidate inference**

- `LIKELY_PARTIAL_REGISTRATION_AFTER_ADVERSE_PROCEEDING`

**Reasoning**

Some goods survived to registration while others did not. This may be consistent with an opposition result, partial refusal, limitation, or another partial adverse decision.

### R5 — Registered less than 3 years, partial/total goods loss

**Pattern**

- registration publication exists;
- first material goods loss occurs within 3 years after registration;
- part or all of goods become code `2`.

**Candidate inference**

- `LIKELY_INVALIDATION_OR_VOLUNTARY_CANCELLATION`

**Reasoning**

Non-use cancellation is generally less plausible before the three-year non-use window. Invalidation or voluntary cancellation becomes comparatively more plausible.

**Important limitation**

This remains probabilistic. Other case-specific procedures can exist.

### R6 — Registered more than 3 years, goods loss

**Pattern**

- registration publication exists;
- goods loss occurs more than 3 years after registration;
- part or all goods become code `2`.

**Candidate inference**

- `LIKELY_NON_USE_CANCELLATION_OR_OTHER_CANCELLATION`

**Reasoning**

After the three-year period, non-use cancellation becomes a materially stronger candidate, while invalidation and voluntary cancellation remain possible.

### R7 — Loss after renewal / grace window

**Pattern**

- registration exists;
- valid_until is known or can be computed from the registration term;
- goods become code `1` and/or `2` after the renewal/grace window has passed;
- no later renewal/restoration evidence exists.

**Candidate inference**

- `LIKELY_NON_RENEWAL_EXPIRATION`

**Reasoning**

Timing strongly supports non-renewal as the cause, but the goods code itself still does not encode the cause.

## 4. Partial vs total outcome must be explicit

Every case-status inference must separately store scope:

- `PARTIAL`
- `TOTAL`
- `UNKNOWN`

Examples:

- some goods become inactive, some remain -> `PARTIAL`
- all reconstructed known goods become inactive -> `TOTAL`

A monthly patch is never itself a complete goods scope. Partial/total determination must use the reconstructed durable goods universe.

## 5. Confidence model

Suggested fields:

- inference_type
- inferred_status
- inferred_cause
- inferred_scope
- confidence_score
- confidence_band (`LOW`, `MEDIUM`, `HIGH`)
- rule_id
- evidence_summary
- evidence_case_dates
- evidence_goods_counts
- evidence_source_packages
- contradicted_by
- model_version
- computed_at

Confidence should increase when multiple independent signals agree.

Example:

`prelim_pub exists + no registration_pub + long delay + all goods code2`

is stronger than:

`all goods code2` alone.

## 6. Contradiction handling

Inference must be reversible and recomputable.

Examples:

- a later registration publication contradicts an earlier `LIKELY_OPPOSITION_LOSS` inference;
- a later renewal/restoration record contradicts `LIKELY_NON_RENEWAL_EXPIRATION`;
- a later official notice identifying the cause supersedes heuristic cause attribution.

Never overwrite the official fact layer with an inference.

## 7. Required validation before production use

Run the rules over a large historical sample and measure:

- rule hit counts;
- distribution by filing/registration year;
- partial vs total outcomes;
- overlap/conflict between rules;
- cases where later facts contradict earlier inference;
- samples manually checked against known CNIPA notices/events.

Rules should be promoted from `EMPIRICAL` to `VALIDATED` only after large-sample cross-checking.

## 8. Design invariant

**Goods codes describe goods. Dates and publication milestones describe procedure. Case status and legal cause are inferred only from the combination of multiple evidence dimensions, with an explicit rule ID and confidence. No heuristic inference may be stored as an official fact.**
