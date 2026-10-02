# GB UKIPO journal 2026/033 PostgreSQL pilot (#855 / #910)

The first database admission is one complete accepted issue, `2026-033`. It is
not a registry-current import. Every notice remains an immutable journal
observation with `journal_observation_only=true` and
`current_state_verified=false`.

The operator accepts only a separately authorized and completed E structured
stage. It rehashes the E issue JSONL and all stage/raw identities, parses every
detail row, and verifies every referenced original image against F by byte size
and SHA-256. It preserves issue/ZIP/member/detail/row lineage, UK versus WO mark
identity, raw journal title and registration-date text, mark text, goods by
class, all applicant/representative occurrences, and ordered visual bindings.

The issue schema and every row are inserted in one PostgreSQL transaction.
Any schema, count, lineage, visual, currentness, or completion mismatch rolls
back the entire issue. Preflight requires all journal pilot tables to be absent.
The pilot authorizes no other journal issue, historical-stock mutation,
ClickHouse write, serving cutover, source cleanup, or current-state assertion.

After the journal E-stage is independently accepted and this PR is merged, the
pilot plan must be frozen from clean live `origin/main`. Exact Apply authority
will be:

```text
GO #855 GB-JOURNAL-PILOT-2026-033 <plan-sha> ISSUE-ONLY-ROLLBACK
```

An independent source-to-database audit is required after Apply. Only then may
an issue-wise plan for the remaining accepted journals be frozen.
