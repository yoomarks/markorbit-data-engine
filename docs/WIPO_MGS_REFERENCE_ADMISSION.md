# WIPO MGS reference-data admission

Data Engine owns the structured WIPO Madrid Goods & Services Manager corpus. Knowledge owns source
access, raw response retention, normalization and durable delivery evidence.

The authenticated admission endpoint is:

```text
POST /api/admin/v2/fact-admissions/reference/wipo-mgs/snapshots
Authorization: Bearer <fact-admission key>
```

It accepts `WIPO_MGS_STRUCTURED_ADMISSION_V1` only. The payload must identify
`MARKORBIT_KNOWLEDGE` as source owner, carry the exact Knowledge raw-evidence canonical URI and
SHA-256, and contain one bounded `WIPO_MGS_SNAPSHOT_V1` language/class snapshot. The server validates
the fixed WIPO endpoint and scope, source identity, class/language consistency, localized term
identity, raw field preservation, jurisdiction status consistency and record uniqueness.

Migration `026_wipo_mgs_reference.sql` creates
`markorbit_facts.wipo_mgs_term_observation` on `hot_global_only`. Rows are append-only observations;
language-specific acceptance and rejection sets are never merged across languages. Replay of the
same source snapshot is idempotent, partial insertion is recoverable by source term ID, and a
different older snapshot for the same language/class is rejected.

The endpoint never installs schema. Apply the ClickHouse migration through the normal operator
process before enabling a publisher. Knowledge source collection and Data Engine publication must
run as separate Jobs with separate credentials. No live WIPO collection or production admission is
authorized merely because this contract exists.

Authenticated integration consumers read the latest admitted snapshot for each language/class
scope through:

```text
GET /api/v1/reference/wipo-mgs/terms
GET /api/v1/reference/wipo-mgs/terms/{source_term_id}?nice_class=1
```

The list endpoint supports bounded text, Nice class, language, jurisdiction and explicit
acceptance-status filters. Results retain the raw and parsed language-specific acceptance fields,
source response/evidence hashes and observed time. The detail endpoint returns only official
localized terms present in the latest admitted language/class snapshots; it never fills a missing
language with machine translation.
