# Issue #340 — P16 Madrid filing tie evidence

Read-only inspection of `apc18840407-20251231-16.zip` found the blocked identity `(75019416, REF, Z1232308)` repeated with the same latest international status date (`2010-07-23`) and same latest entry number (`192589`). The remaining source-level difference is the renewal date: one row has `2019-09-23`, the corrected later row has `2029-09-23`.

The package failed before any final-table commit. Production evidence at the block boundary showed P16 canary `STAGING/STARTED`, all 12 commits `PENDING`, and all 12 P16 final package counts equal to zero.

The proposed current-snapshot tie-break preserves the existing order (`latest status date` → `latest entry number`) and adds `latest renewal date` only when those two dimensions tie. Any records still differing after renewal-date selection remain fail-closed via the existing stable-hash ambiguity check.
