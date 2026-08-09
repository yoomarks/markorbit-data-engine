# US M1.3 Historical Part Completeness

This note freezes the strict historical coverage-part acceptance rule used by `app.us.audit_real_data_v2`.

- Historical part numbering starts at `01`.
- Observed parts must be continuous from `01` through the observed maximum.
- The total historical part count is never inferred from the highest observed suffix.
- Strict acceptance requires an explicit expected total via `-ExpectedHistoryParts N` / `--expected-history-parts N`.
- With a pinned total `N`, the latest historical coverage range must contain exactly `01..N`.
- Missing `01`, an interior gap, a missing pinned tail, part `00`, or an observed suffix above `N` keeps acceptance at `NOT_READY`.
- These checks validate source completeness only; they do not infer any trademark legal status.
