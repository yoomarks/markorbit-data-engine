# GB UKIPO journal E structured-stage relocation (#855 / #910)

The accepted UKIPO journal handoff contains 78 issues from `2025-014` through
`2025-052` and `2026-001` through `2026-039`. Its structured manifests and
JSONL were staged on D before the post-#837 storage topology was accepted. D is
now `hot_cn` only, so GB structured/query data must be resident on E before any
`hot_global` database admission.

`app.global_trademarks.gb_journal_e_stage` is a narrow relocation operator. It
verifies the exact accepted independent journal audit, missing-image audit,
per-issue manifest and JSONL identities, all 78 official raw ZIP identities on
F, and the accepted F original-visual relocation receipt. It then supports an
additive, resumable, byte-identical copy of only the 156 structured-stage files
from D to E.

Existing target files are reused only when size and SHA-256 match. A complete
`.partial` file can be atomically renamed after verification; incomplete,
mismatching, or unexpected target state fails closed and is not overwritten.
D structured source evidence, F raw ZIPs, and F original visuals remain in
place. The physical E reserve floor is checked before and after copy.

The frozen relocation plan authorizes no PostgreSQL or ClickHouse write, VHDX
operation, file deletion, current-register assertion, or serving cutover. Exact
copy authority is:

```text
GO #855 GB-JOURNAL-E-STAGE <plan-sha> ADDITIVE-COPY-VERIFY-NO-DELETE
```

Journal rows remain observation-only. They preserve issue/source lineage and
must use `current_state_verified=false`; publication evidence is not equivalent
to current registry truth. Database admission requires a later, separately
frozen plan and exact authority token after this E stage is accepted.
