# US M1.3 Read API

`GET /api/us/cases/{serial_number}` exposes the complete currently modeled USPTO official fact bundle for one eight-digit serial number.

The response includes:

- `case`
- `owners`
- `classifications`
- `events`
- `statements`
- `correspondent`
- `design_searches`
- `prior_registrations`
- `foreign_applications`
- `madrid_filings`
- `madrid_events`

`correspondent` is a single object or `null`; the other subordinate families are arrays. Current families are read with `FINAL` and `is_deleted = 0`. General event history and Madrid event history are cumulative evidence and are not filtered through snapshot tombstones.

`GET /api/us/summary` reports row counts for all 11 US M1.3 durable fact tables.

Every US case response retains:

```text
status_semantics = OFFICIAL_RAW_NOT_LEGAL_INTERPRETATION
```

The API does not infer `has_attorney`, `is_pro_se`, attorney roles, ACTIVE/DEAD status, Section 8 compliance, renewal eligibility, or other legal conclusions.

The live `US_M1.3_OFFICIAL_FACT_FAMILIES_FIXTURE` now publishes the six M1.3 fact families into ClickHouse and calls the actual `us_case()` read path before cleanup, so CI validates both storage and read exposure against the same isolated official-fact dataset.
