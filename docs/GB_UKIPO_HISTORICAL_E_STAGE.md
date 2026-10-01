# GB UKIPO historical E structured-stage relocation (#855)

The accepted UKIPO historical stock JSONL was originally staged under D before
the post-#837 storage topology was corrected. D is now `hot_cn` only. GB and LA
structured/query data belong on E and future serving projection belongs on
`hot_global`; official raw ZIPs and original visual/logo evidence remain F
authority.

`app.global_trademarks.gb_historical_e_stage` is the narrow bridge from the
accepted legacy stage to the corrected placement. It verifies:

- the accepted independent two-ZIP audit SHA;
- all four accepted/quarantine JSONL byte sizes and SHA-256 digests;
- the two exact official F ZIP sizes and SHA-256 digests;
- the E physical reserve floor before and after copy.

The Apply operation is additive and resumable. Existing targets are reused only
when size and SHA match exactly. A complete `.partial` file can be atomically
renamed after verification; incomplete or mismatching state fails closed for
manual review and is never overwritten. D and F sources are retained.

The frozen plan authorizes no PostgreSQL or ClickHouse write, VHDX operation,
file deletion, current-register assertion, or serving cutover. Exact authority:

```text
GO #855 GB-HISTORICAL-E-STAGE <plan-sha> ADDITIVE-COPY-VERIFY-NO-DELETE
```

After authorized Apply, a separate independent E-vs-accepted-stage audit and a
new E-bound full-resume plan are required. The 2018-era source status remains
`historical_source_only=true` and `current_state_verified=false`.
